from __future__ import annotations

import io
import sqlite3
import struct
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import piexif
import pytest
from PIL import Image

from photocluster.scanner import CACHE_FILENAME, _extract_exif, _gps_to_decimal, _init_cache, scan


# ---------------------------------------------------------------------------
# GPS conversion
# ---------------------------------------------------------------------------


def test_gps_to_decimal_north():
    coords = ((43, 1), (0, 1), (0, 1))
    assert _gps_to_decimal(coords, b"N") == pytest.approx(43.0)


def test_gps_to_decimal_south():
    coords = ((43, 1), (30, 1), (0, 1))
    assert _gps_to_decimal(coords, b"S") == pytest.approx(-43.5)


def test_gps_to_decimal_west():
    coords = ((16, 1), (27, 1), (36, 1))
    expected = -(16 + 27 / 60 + 36 / 3600)
    assert _gps_to_decimal(coords, b"W") == pytest.approx(expected)


def test_gps_to_decimal_string_ref():
    coords = ((10, 1), (0, 1), (0, 1))
    assert _gps_to_decimal(coords, "S") == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_init_cache_creates_table(tmp_path):
    db_path = tmp_path / "cache.db"
    conn = _init_cache(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scan_cache'")
    assert cursor.fetchone() is not None
    conn.close()


def test_init_cache_idempotent(tmp_path):
    db_path = tmp_path / "cache.db"
    conn1 = _init_cache(db_path)
    conn1.close()
    conn2 = _init_cache(db_path)  # should not raise
    conn2.close()


# ---------------------------------------------------------------------------
# EXIF extraction
# ---------------------------------------------------------------------------


def _make_jpeg_with_exif(path: Path, dt_str: str = "2024:07:14 10:30:00", lat=None, lon=None) -> None:
    img = Image.new("RGB", (10, 10), color=(128, 0, 0))
    exif_dict: dict = {"0th": {}, "Exif": {}, "GPS": {}}
    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = dt_str.encode()
    if lat is not None and lon is not None:
        lat_abs = abs(lat)
        lon_abs = abs(lon)
        exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = b"S" if lat < 0 else b"N"
        exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = (
            (int(lat_abs), 1), (0, 1), (0, 1)
        )
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b"W" if lon < 0 else b"E"
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = (
            (int(lon_abs), 1), (0, 1), (0, 1)
        )
    exif_bytes = piexif.dump(exif_dict)
    img.save(path, format="JPEG", exif=exif_bytes)


def test_extract_exif_timestamp(tmp_path):
    jpg = tmp_path / "photo.jpg"
    _make_jpeg_with_exif(jpg, "2024:07:14 10:30:00")
    ts, lat, lon = _extract_exif(jpg)
    assert ts == datetime(2024, 7, 14, 10, 30, 0)
    assert lat is None
    assert lon is None


def test_extract_exif_gps(tmp_path):
    jpg = tmp_path / "photo.jpg"
    _make_jpeg_with_exif(jpg, lat=42.0, lon=18.0)
    ts, lat, lon = _extract_exif(jpg)
    assert lat == pytest.approx(42.0)
    assert lon == pytest.approx(18.0)


def test_extract_exif_south_west(tmp_path):
    jpg = tmp_path / "photo.jpg"
    _make_jpeg_with_exif(jpg, lat=-33.0, lon=-70.0)
    _, lat, lon = _extract_exif(jpg)
    assert lat == pytest.approx(-33.0)
    assert lon == pytest.approx(-70.0)


def test_extract_exif_invalid_file(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    ts, lat, lon = _extract_exif(bad)
    assert ts is None
    assert lat is None
    assert lon is None


# ---------------------------------------------------------------------------
# scan() with caching
# ---------------------------------------------------------------------------


def test_scan_finds_images(tmp_path):
    _make_jpeg_with_exif(tmp_path / "a.jpg")
    _make_jpeg_with_exif(tmp_path / "b.jpg")
    (tmp_path / "notes.txt").write_text("ignore me")

    photos = scan(tmp_path)
    assert len(photos) == 2
    assert all(p.path.suffix == ".jpg" for p in photos)


def test_scan_cache_hit(tmp_path):
    jpg = tmp_path / "photo.jpg"
    _make_jpeg_with_exif(jpg, "2024:07:14 10:30:00")

    photos1 = scan(tmp_path)
    assert len(photos1) == 1

    # Patch _extract_exif to ensure it is NOT called on the second scan
    with patch("photocluster.scanner._extract_exif") as mock_exif:
        photos2 = scan(tmp_path)
        mock_exif.assert_not_called()

    assert photos2[0].timestamp == photos1[0].timestamp


def test_scan_recursive(tmp_path):
    subdir = tmp_path / "trip"
    subdir.mkdir()
    _make_jpeg_with_exif(tmp_path / "root.jpg")
    _make_jpeg_with_exif(subdir / "sub.jpg")

    photos = scan(tmp_path)
    paths = {p.path for p in photos}
    assert tmp_path / "root.jpg" in paths
    assert subdir / "sub.jpg" in paths
