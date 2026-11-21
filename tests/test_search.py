from datetime import date

from safelight.search import PhotoFilters, build_search


def test_no_filter_means_no_search():
    assert build_search(PhotoFilters()) is None


def test_single_filter_is_a_single_criterion():
    assert build_search(PhotoFilters(rating_min=4)) == {
        "criteria": "rating",
        "operation": ">=",
        "value": 4,
    }


def test_filters_are_combined_with_intersect_items():
    search = build_search(
        PhotoFilters(
            rating_min=3,
            pick="picked",
            label="blue",
            camera="ILCE-7M3",
            keywords="paris night",
            captured_from=date(2026, 11, 1),
            captured_to=date(2026, 11, 19),
            has_gps=False,
        )
    )
    assert search["combine"] == "intersect"
    items = search["items"]
    assert {"criteria": "rating", "operation": ">=", "value": 3} in items
    assert {"criteria": "pick", "operation": "==", "value": 1} in items
    assert {"criteria": "labelColor", "operation": "==", "value": 4} in items
    assert {"criteria": "camera", "operation": "==", "value": "ILCE-7M3"} in items
    assert {"criteria": "keywords", "operation": "all", "value": "paris night"} in items
    # Lightroom's "after"/"before" are strict: the range includes both days.
    assert {"criteria": "captureTime", "operation": ">", "value": "2026-10-31"} in items
    assert {"criteria": "captureTime", "operation": "<", "value": "2026-11-20"} in items
    assert {"criteria": "hasGPSData", "operation": "isFalse", "value": True} in items


def test_recent_days():
    assert build_search(PhotoFilters(captured_in_last_days=7)) == {
        "criteria": "captureTime",
        "operation": "inLast",
        "value": 7,
        "value_units": "days",
    }
