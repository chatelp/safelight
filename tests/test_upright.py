import io
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

from safelight.upright import (
    RESET_SETTINGS,
    fit_rotation,
    guide_errors,
    homography,
    plan,
    plan_from_jpeg,
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
