import io

import pytest
from PIL import Image, ImageDraw, ImageFilter

from safelight.analysis import ImageStats, findings, groups, hamming, measure, top_of_stacks
from safelight.search import PhotoFilters, has_post_filters, matches_post_filters


def jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def scene(tilt: float = 0.0) -> Image.Image:
    """A horizon with fence posts, rotated by `tilt` degrees (counterclockwise +)."""
    base = Image.new("RGB", (1600, 1100), (150, 160, 170))
    draw = ImageDraw.Draw(base)
    draw.rectangle((0, 560, 1600, 1100), fill=(60, 70, 60))
    for x in range(100, 1600, 180):
        draw.rectangle((x, 300, x + 14, 560), fill=(40, 40, 40))
    rotated = base.rotate(tilt, resample=Image.Resampling.BICUBIC, fillcolor=(150, 160, 170))
    return rotated.crop((250, 200, 1350, 900))


@pytest.mark.parametrize("tilt", [0.0, 1.0, 3.0, -2.0, 5.0])
def test_tilt_is_measured_within_a_few_tenths_of_a_degree(tilt):
    measured = measure(jpeg(scene(tilt))).tilt_deg
    assert measured is not None
    assert measured == pytest.approx(tilt, abs=0.3)


@pytest.mark.parametrize("tilt", [0.0, 2.0, -3.0])
def test_a_lone_horizon_is_enough(tilt):
    base = Image.new("RGB", (1600, 1100), (150, 170, 200))
    ImageDraw.Draw(base).rectangle((0, 560, 1600, 1100), fill=(40, 70, 110))
    image = base.rotate(tilt, resample=Image.Resampling.BICUBIC, fillcolor=(150, 170, 200))
    measured = measure(jpeg(image.crop((250, 200, 1350, 900)))).tilt_deg
    assert measured == pytest.approx(tilt, abs=0.3)


def test_blur_lowers_focus_a_lot():
    sharp = measure(jpeg(scene()))
    blurred = measure(jpeg(scene().filter(ImageFilter.GaussianBlur(4))))
    assert blurred.focus < 0.2 * sharp.focus


def test_clipping_and_brightness():
    white = measure(jpeg(Image.new("RGB", (400, 300), (255, 255, 255))), with_tilt=False)
    black = measure(jpeg(Image.new("RGB", (400, 300), (0, 0, 0))), with_tilt=False)
    assert white.clipped_highlights == 1.0 and white.brightness > 0.95
    assert black.clipped_shadows == 1.0 and black.brightness < 0.05


def test_same_picture_same_hash_and_different_scenes_differ():
    a = measure(jpeg(scene()), with_tilt=False)
    b = measure(jpeg(scene()), with_tilt=False)
    other = measure(jpeg(scene().transpose(Image.Transpose.FLIP_TOP_BOTTOM)), with_tilt=False)
    assert hamming(a.dhash, b.dhash) == 0
    assert hamming(a.dhash, other.dhash) > 10


def _stats(focus: float, dhash: int, highlights: float = 0.0) -> ImageStats:
    return ImageStats(focus, 0.5, highlights, 0.0, None, dhash)


def test_bursts_need_close_times_and_similar_frames():
    photos = [
        {"uuid": "a", "captured_at": "2026-11-20T10:00:00+01:00", "camera": "X"},
        {"uuid": "b", "captured_at": "2026-11-20T10:00:01+01:00", "camera": "X"},
        {"uuid": "c", "captured_at": "2026-11-20T10:00:02+01:00", "camera": "X"},
        {"uuid": "d", "captured_at": "2026-11-20T10:00:03+01:00", "camera": "X"},
        {"uuid": "e", "captured_at": "2026-11-20T11:00:00+01:00", "camera": "X"},
    ]
    stats = {
        "a": _stats(50, 0b1111),
        "b": _stats(90, 0b1110),  # sharpest of the burst
        "c": _stats(70, 0b1111, highlights=0.2),
        "d": _stats(80, (1 << 63) - 1),  # 1 s later but a different picture
        "e": _stats(60, 0b1111),  # an hour later, same picture: a duplicate
    }
    found = groups(photos, stats, max_gap_s=2.0)
    assert len(found) == 1
    group = found[0]
    assert group["kind"] == "burst"
    assert set(group["photo_ids"]) == {"a", "b", "c", "e"}
    assert group["best_photo_id"] == "b"


def test_picked_frame_wins_a_close_call():
    photos = [
        {"uuid": "a", "captured_at": "2026-11-20T10:00:00", "pick": 1},
        {"uuid": "b", "captured_at": "2026-11-20T10:00:01", "pick": 0},
    ]
    stats = {"a": _stats(95, 0), "b": _stats(100, 0)}
    assert groups(photos, stats)[0]["best_photo_id"] == "a"


def test_findings_flag_blur_relative_to_the_set_and_exposure():
    photos = [{"uuid": str(i)} for i in range(6)]
    stats = {str(i): _stats(100, i) for i in range(6)}
    stats["0"] = _stats(20, 0)
    stats["1"] = _stats(100, 1, highlights=0.1)
    reports = {r.photo_id: r.issues for r in findings(photos, stats, {"sharpness", "exposure"})}
    assert any("blurred" in issue for issue in reports["0"])
    assert any("highlights" in issue for issue in reports["1"])
    assert reports["2"] == []


def test_post_filters_on_focal_length_aperture_and_shutter():
    filters = PhotoFilters(focal_length_min_mm=50, aperture_max=4, shutter_max_s=1 / 200)
    assert has_post_filters(filters)
    assert matches_post_filters(
        {"focal_length_mm": 70, "aperture": 4.0, "shutter_s": 0.0050000006}, filters
    )
    assert not matches_post_filters(
        {"focal_length_mm": 35, "aperture": 4.0, "shutter_s": 0.001}, filters
    )
    assert not matches_post_filters(
        {"focal_length_mm": 70, "aperture": 5.6, "shutter_s": 0.001}, filters
    )
    assert not matches_post_filters({"aperture": 2.8}, filters)
    assert not has_post_filters(PhotoFilters(rating_min=3))


def test_empty_border_left_by_a_perspective_correction():
    image = scene()
    ImageDraw.Draw(image).polygon([(0, 0), (1100, 0), (1100, 30)], fill=(255, 255, 255))
    assert measure(jpeg(image), with_tilt=False).blank_edges == ("top",)
    assert measure(jpeg(scene()), with_tilt=False).blank_edges == ()
    sky = Image.new("RGB", (1100, 700), (255, 255, 255))  # a white sky is not a border
    assert measure(jpeg(sky), with_tilt=False).blank_edges == ()


def test_frames_under_a_stack_are_left_out():
    photos = [
        {"uuid": "hdr", "stack_position": 1},
        {"uuid": "bracket-1", "stack_position": 2},
        {"uuid": "bracket-2", "stack_position": 3},
        {"uuid": "single"},
    ]
    kept, skipped = top_of_stacks(photos)
    assert [p["uuid"] for p in kept] == ["hdr", "single"]
    assert skipped == 2
