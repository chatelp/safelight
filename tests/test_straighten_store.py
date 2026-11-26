import io
import os
import time

from PIL import Image

from safelight import straighten_store
from safelight.upright import Line, plan_from_lines


def _jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (3000, 2000), (90, 90, 90)).save(buffer, "JPEG")
    return buffer.getvalue()


def _plan():
    lines = [Line((0.1, 0.1, 0.1, 0.9), 0.8, 0.0), Line((0.9, 0.1, 0.9, 0.9), 0.8, 0.0)]
    return plan_from_lines(lines, [0, 1], 6000, 4000, 2833.0)


def test_kept_privately_small_and_not_forever(tmp_path):
    folder = straighten_store.store_dir(tmp_path)
    folder.mkdir(parents=True)
    old = folder / "OLD.jpg"
    old.write_bytes(b"x")
    long_ago = time.time() - (straighten_store.KEEP_DAYS + 1) * 86400
    os.utime(old, (long_ago, long_ago))

    straighten_store.save("A/1", _plan(), {"LensProfileEnable": 0}, _jpeg(), tmp_path)
    saved = straighten_store.load("A/1", tmp_path)

    assert folder.stat().st_mode & 0o777 == 0o700
    assert not old.exists()
    assert saved is not None and len(saved.lines) == 2
    assert max(Image.open(io.BytesIO(saved.jpeg)).size) == 1600
    assert saved.lens_changed({"LensProfileEnable": 1})
    assert not saved.lens_changed({"LensProfileEnable": 0})
    assert straighten_store.load("missing", tmp_path) is None
