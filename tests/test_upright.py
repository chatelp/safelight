import io
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

from safelight.upright import (
    RESET_SETTINGS,
    Line,
    _project,
    disagreement_deg,
    exact_homography,
    fit_rotation,
    guide_errors,
    homography,
    plan,
    plan_from_jpeg,
    plan_from_lines,
)

W, H, F = 6000, 4000, 17 / 36 * 6000


def keystoned_rectangle(pitch_deg, yaw_deg, roll_deg):
    """Edges of a frontal rectangle as seen by a camera turned by these angles."""
    camera = np.linalg.inv(
        homography(*(math.radians(a) for a in (pitch_deg, yaw_deg, roll_deg)), W, H, F)
    )

    def seen(x, y):
        q = camera @ np.array([x, y, 1.0])
        return tuple(q[:2] / q[2])

    corners = [seen(0.2, 0.3), seen(0.8, 0.3), seen(0.8, 0.7), seen(0.2, 0.7)]
    (a, b, c, d) = corners
    return [(*a, *d), (*b, *c), (*a, *b), (*d, *c)]


@pytest.mark.parametrize("angles", [(3.0, 0.0, 0.0), (-2.0, 1.5, 0.5), (0.0, -3.0, -1.0)])
def test_rotation_is_recovered_from_four_guides(angles):
    segments = keystoned_rectangle(*angles)
    rotation = fit_rotation(segments, W, H, F)
    straightened = homography(*rotation, W, H, F)
    assert max(abs(math.degrees(e)) for e in guide_errors(straightened, segments)) < 0.01


def test_vertical_guides_only_leave_yaw_alone():
    segments = keystoned_rectangle(4.0, 0.0, 0.0)[:2]
    pitch, yaw, _roll = fit_rotation(segments, W, H, F)
    assert yaw == 0.0
    assert abs(math.degrees(pitch)) == pytest.approx(4.0, abs=0.05)


def test_matrix_keeps_the_centre_in_place():
    m = homography(math.radians(5), math.radians(2), 0.0, W, H, F)
    q = m @ np.array([0.5, 0.5, 1.0])
    assert q[:2] / q[2] == pytest.approx([0.5, 0.5], abs=1e-9)


def exhibition_wall(pitch_deg):
    """A print on a wall, photographed with the camera tilted."""
    flat = Image.new("RGB", (1500, 1000), (40, 40, 45))
    draw = ImageDraw.Draw(flat)
    draw.rectangle((300, 300, 1200, 700), fill=(230, 230, 225))
    draw.rectangle((380, 360, 760, 640), fill=(90, 120, 160))
    draw.rectangle((90, 0, 100, 1000), fill=(120, 120, 120))  # a wall corner
    camera = np.linalg.inv(homography(math.radians(pitch_deg), 0.0, 0.0, 1500, 1000, F / 4))
    # PIL wants the map from output pixels to input pixels
    to_unit = np.diag([1 / 1500, 1 / 1000, 1.0])
    back = np.linalg.inv(to_unit) @ np.linalg.inv(camera) @ to_unit
    coeffs = (back / back[2, 2]).flatten()[:8]
    return flat.transform(flat.size, Image.Transform.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)


def test_plan_straightens_a_tilted_exhibition_photo():
    image = exhibition_wall(4.0)
    result = plan(image, 1500, 1000, focal_35mm=17)
    assert result is not None
    assert result.guide_error_deg < 0.3
    assert abs(result.pitch_deg) == pytest.approx(4.0, abs=0.6)
    assert 2 <= len(result.guides) <= 4
    settings = result.settings()
    assert settings["PerspectiveUpright"] == 5
    assert settings["UprightFourSegmentsCount"] == len(result.guides)
    assert len(settings["UprightTransform_5"].split(",")) == 9
    assert settings["PerspectiveVertical"] == RESET_SETTINGS["PerspectiveVertical"] == 0


def test_no_lines_no_plan():
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (128, 128, 128)).save(buffer, "JPEG")
    assert plan_from_jpeg(buffer.getvalue(), {"width": 6000, "height": 4000}) is None


def test_vertical_guides_are_taken_far_apart():
    from safelight.upright import Line, _pick_guides

    near_left = Line((0.50, 0.0, 0.49, 0.3), 0.30, -1.4)
    near_right = Line((0.55, 0.3, 0.55, 0.8), 0.46, -0.1)
    far_left = Line((0.11, 0.46, 0.11, 0.64), 0.18, -0.4)
    far_right = Line((0.96, 0.27, 0.96, 0.52), 0.24, -1.2)
    guides = _pick_guides([near_left, near_right, far_left, far_right], [])
    assert len(guides) == 2
    assert abs(guides[0].center[0] - guides[1].center[0]) > 0.4


def test_a_single_horizontal_does_not_turn_the_view():
    image = exhibition_wall(3.0)
    result = plan(image, 1500, 1000, focal_35mm=17)
    assert result is not None
    assert (
        all(abs(g[3] - g[1]) > abs(g[2] - g[0]) for g in result.guides)
        or len([g for g in result.guides if abs(g[3] - g[1]) <= abs(g[2] - g[0])]) >= 2
    )


# -- exact correction and chosen lines ----------------------------------------------------


def shifted_rectangle(pitch_deg, yaw_deg, roll_deg, shift=(0.03, -0.04)):
    """Edges of a frontal rectangle seen through a shifted lens (tilt-shift): the optical
    centre is not the image centre, which a camera rotation alone cannot model."""
    move = np.array([[1, 0, shift[0]], [0, 1, shift[1]], [0, 0, 1.0]])
    rotation = homography(*(math.radians(a) for a in (pitch_deg, yaw_deg, roll_deg)), W, H, F)
    camera = np.linalg.inv(move) @ np.linalg.inv(rotation) @ move

    def seen(x, y):
        q = camera @ np.array([x, y, 1.0])
        return tuple(q[:2] / q[2])

    a, b, c, d = seen(0.2, 0.3), seen(0.8, 0.3), seen(0.8, 0.7), seen(0.2, 0.7)
    return [(*a, *d), (*b, *c), (*a, *b), (*d, *c)]


def _worst(matrix, segments):
    return max(abs(math.degrees(e)) for e in guide_errors(matrix, segments))


def _aspect(matrix):
    centre = np.array(_project(matrix, 0.5, 0.5))
    across = (np.array(_project(matrix, 0.5001, 0.5)) - centre) * W
    down = (np.array(_project(matrix, 0.5, 0.5001)) - centre) * H
    return np.linalg.norm(across) / np.linalg.norm(down)


def test_exact_correction_straightens_what_a_rotation_cannot():
    segments = shifted_rectangle(4.0, 2.0, 0.5)
    rotation = homography(*fit_rotation(segments, W, H, F), W, H, F)
    exact = exact_homography(segments, W, H, rotation)
    assert _worst(rotation, segments) > 0.1  # the shifted lens is out of reach
    assert exact is not None
    assert _worst(exact, segments) < 1e-6
    # Proportions stay those of the rotation: a print is not stretched.
    assert _aspect(exact) == pytest.approx(_aspect(rotation), rel=2e-3)


def test_exact_correction_is_refused_for_lines_of_different_views():
    segments = keystoned_rectangle(1.0, 0.0, 0.0)
    x1, y1, x2, y2 = segments[3]
    segments[3] = (x1, y1 + 0.25, x2, y2 - 0.25)  # a ceiling rail in steep perspective
    rotation = homography(*fit_rotation(segments, W, H, F), W, H, F)
    assert exact_homography(segments, W, H, rotation) is None
    assert exact_homography(segments[:3], W, H, rotation) is None  # needs 2 + 2 lines


def _lines(segments):
    return [Line(s, 0.4, 0.0) for s in segments]


def test_chosen_lines_make_an_exact_plan():
    lines = _lines([*shifted_rectangle(3.0, 1.0, 0.0), (0.5, 0.1, 0.5, 0.2)])
    result = plan_from_lines(lines, [0, 1, 2, 3], W, H, F)
    assert result.model == "exact"
    assert result.guide_error_deg < 1e-6
    assert result.guide_lines == [0, 1, 2, 3]
    only_verticals = plan_from_lines(lines, [0, 1], W, H, F)
    assert only_verticals.model == "rotation"


@pytest.mark.parametrize(
    ("chosen", "message"),
    [
        ([0], "two to four"),
        ([0, 0], "at most once"),
        ([0, 9], "from 1 to 5"),
        ([2, 3], "vertical"),
        ([0, 4], "too close"),
    ],
)
def test_chosen_lines_are_checked(chosen, message):
    lines = _lines([*shifted_rectangle(3.0, 1.0, 0.0), (0.25, 0.1, 0.25, 0.9)])
    with pytest.raises(ValueError, match=message):
        plan_from_lines(lines, chosen, W, H, F)


def test_disagreement_between_two_choices_of_verticals():
    segments = keystoned_rectangle(4.0, 0.0, 0.0)
    good = homography(*fit_rotation(segments[:2], W, H, F), W, H, F)
    leaning = [(0.3, 0.2, 0.33, 0.8), (0.7, 0.2, 0.69, 0.8)]  # e.g. a shadow and a cable
    other = homography(*fit_rotation(leaning, W, H, F), W, H, F)
    assert disagreement_deg(good, segments[:2], good, segments[:2]) < 1e-6
    assert disagreement_deg(good, segments[:2], other, leaning) > 1.0


def test_chosen_print_edges_give_the_print_its_true_shape():
    image = exhibition_wall(4.0)
    found = plan(image, 1500, 1000, focal_35mm=17)
    assert found is not None
    edges = {}  # the print is 0.2..0.8 wide and 0.3..0.7 high on the wall
    for index, line in enumerate(found.lines):
        x, y = line.center
        if line.length > 0.3 and (0.15 < x < 0.85) and (0.25 < y < 0.75):
            side = (
                ("left" if x < 0.5 else "right")
                if line.vertical
                else "top"
                if y < 0.5
                else "bottom"
            )
            edges.setdefault(side, index)
    assert set(edges) == {"left", "right", "top", "bottom"}
    chosen = plan_from_lines(found.lines, list(edges.values()), 1500, 1000, found.focal_px)
    assert chosen.model == "exact"
    corners = {
        side: _project(chosen.matrix, *found.lines[index].segment[:2])
        for side, index in edges.items()
    }
    assert corners["left"][0] == pytest.approx(0.2, abs=0.003)
    assert corners["right"][0] == pytest.approx(0.8, abs=0.003)
    assert corners["top"][1] == pytest.approx(0.3, abs=0.003)
    assert corners["bottom"][1] == pytest.approx(0.7, abs=0.003)
