from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from photocluster.clusterer import (
    _centroid,
    _default_cluster,
    _split_by_radius,
    cluster_photos,
    haversine_km,
    parse_time_gap,
)
from photocluster.models import Photo


# ---------------------------------------------------------------------------
# parse_time_gap
# ---------------------------------------------------------------------------


def test_parse_hours():
    assert parse_time_gap("48h") == timedelta(hours=48)


def test_parse_days():
    assert parse_time_gap("3d") == timedelta(days=3)


def test_parse_float_days():
    assert parse_time_gap("1.5d") == timedelta(days=1.5)


def test_parse_invalid():
    with pytest.raises(ValueError):
        parse_time_gap("2w")

    with pytest.raises(ValueError):
        parse_time_gap("abc")

    with pytest.raises(ValueError):
        parse_time_gap("")


# ---------------------------------------------------------------------------
# haversine_km
# ---------------------------------------------------------------------------


def test_haversine_same_point():
    assert haversine_km(0, 0, 0, 0) == pytest.approx(0.0)


def test_haversine_london_paris():
    # London (51.5074, -0.1278) ↔ Paris (48.8566, 2.3522) ≈ 343 km
    dist = haversine_km(51.5074, -0.1278, 48.8566, 2.3522)
    assert 335 < dist < 355


def test_haversine_equator():
    # 1 degree longitude on equator ≈ 111.32 km
    dist = haversine_km(0, 0, 0, 1)
    assert 110 < dist < 113


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _photo(ts: datetime | None, lat: float | None = None, lon: float | None = None, n: int = 0) -> Photo:
    return Photo(
        path=Path(f"/src/photo_{n}.jpg"),
        mtime=0.0,
        timestamp=ts,
        lat=lat,
        lon=lon,
    )


# ---------------------------------------------------------------------------
# _centroid
# ---------------------------------------------------------------------------


def test_centroid_no_gps():
    photos = [_photo(None), _photo(None)]
    assert _centroid(photos) == (None, None)


def test_centroid_single():
    photos = [_photo(None, lat=10.0, lon=20.0)]
    lat, lon = _centroid(photos)
    assert lat == pytest.approx(10.0)
    assert lon == pytest.approx(20.0)


def test_centroid_average():
    photos = [_photo(None, 0.0, 0.0), _photo(None, 10.0, 20.0)]
    lat, lon = _centroid(photos)
    assert lat == pytest.approx(5.0)
    assert lon == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# _split_by_radius
# ---------------------------------------------------------------------------


def test_split_no_gps_keeps_all():
    photos = [_photo(None) for _ in range(3)]
    groups = _split_by_radius(photos, radius_km=50.0)
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_split_all_within_radius():
    # All photos near (0,0)
    photos = [_photo(None, 0.01 * i, 0.01 * i) for i in range(5)]
    groups = _split_by_radius(photos, radius_km=200.0)
    assert len(groups) == 1


def test_split_outlier():
    # 4 photos near (0,0) and one far away (~5000 km)
    near = [_photo(None, 0.0, 0.01 * i, n=i) for i in range(4)]
    far = [_photo(None, 45.0, 45.0, n=99)]
    groups = _split_by_radius(near + far, radius_km=50.0)
    assert len(groups) == 2
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 4]


# ---------------------------------------------------------------------------
# _default_cluster
# ---------------------------------------------------------------------------


def test_default_cluster_single_gap():
    t0 = datetime(2024, 7, 1, 10, 0)
    photos = [
        _photo(t0, n=0),
        _photo(t0 + timedelta(hours=1), n=1),
        _photo(t0 + timedelta(days=3), n=2),  # gap > 48 h
        _photo(t0 + timedelta(days=3, hours=1), n=3),
    ]
    groups = _default_cluster(photos, time_gap=timedelta(hours=48), radius_km=500.0)
    assert len(groups) == 2
    assert len(groups[0]) == 2
    assert len(groups[1]) == 2


def test_default_cluster_empty():
    assert _default_cluster([], time_gap=timedelta(hours=48), radius_km=50.0) == []


# ---------------------------------------------------------------------------
# cluster_photos
# ---------------------------------------------------------------------------


def test_cluster_photos_locked_vs_root(tmp_path):
    # Create one photo at source root, one in a subdirectory
    subdir = tmp_path / "existing-trip"
    subdir.mkdir()

    root_photo = _photo(datetime(2024, 7, 1, 10, 0), n=0)
    root_photo = Photo(path=tmp_path / "root.jpg", mtime=0.0, timestamp=datetime(2024, 7, 1), lat=None, lon=None)

    sub_photo = Photo(path=subdir / "sub.jpg", mtime=0.0, timestamp=datetime(2024, 7, 2), lat=None, lon=None)

    clusters = cluster_photos(
        [root_photo, sub_photo],
        source=tmp_path,
        time_gap=timedelta(hours=48),
        radius_km=50.0,
    )

    locked = [c for c in clusters if c.locked]
    unlocked = [c for c in clusters if not c.locked]

    assert len(locked) == 1
    assert locked[0].name == "existing-trip"
    assert len(unlocked) == 1


def test_cluster_photos_untimed(tmp_path):
    timed = Photo(path=tmp_path / "a.jpg", mtime=0.0, timestamp=datetime(2024, 7, 1), lat=None, lon=None)
    untimed = Photo(path=tmp_path / "b.jpg", mtime=0.0, timestamp=None, lat=None, lon=None)

    clusters = cluster_photos([timed, untimed], source=tmp_path, time_gap=timedelta(hours=48), radius_km=50.0)
    names = [c.name for c in clusters]
    assert "Untitled" in names


def test_cluster_photos_time_split(tmp_path):
    t0 = datetime(2024, 7, 1, 10, 0)
    photos = [
        Photo(path=tmp_path / f"img{i}.jpg", mtime=0.0, timestamp=t0 + timedelta(hours=i * 2), lat=None, lon=None)
        for i in range(3)
    ] + [
        Photo(path=tmp_path / f"img{i+10}.jpg", mtime=0.0, timestamp=t0 + timedelta(days=4, hours=i), lat=None, lon=None)
        for i in range(2)
    ]

    clusters = cluster_photos(photos, source=tmp_path, time_gap=timedelta(hours=48), radius_km=50.0)
    unlocked = [c for c in clusters if not c.locked]
    assert len(unlocked) == 2
