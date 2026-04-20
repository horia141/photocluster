from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import piexif
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from .models import Photo

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".tiff", ".tif", ".heic", ".png", ".webp"}
CACHE_FILENAME = ".photocluster_cache.db"


def _init_cache(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_cache (
            path TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            timestamp TEXT,
            lat REAL,
            lon REAL
        )
    """)
    conn.commit()
    return conn


def _gps_to_decimal(coords: tuple, ref: bytes | str) -> float:
    d, m, s = coords
    decimal = d[0] / d[1] + m[0] / (m[1] * 60) + s[0] / (s[1] * 3600)
    if ref in (b"S", b"W", "S", "W"):
        decimal = -decimal
    return decimal


def _extract_exif(path: Path) -> tuple[Optional[datetime], Optional[float], Optional[float]]:
    timestamp: Optional[datetime] = None
    lat: Optional[float] = None
    lon: Optional[float] = None

    try:
        exif_data = piexif.load(str(path))

        exif_ifd = exif_data.get("Exif", {})
        dt_bytes = exif_ifd.get(piexif.ExifIFD.DateTimeOriginal)
        if not dt_bytes:
            ifd0 = exif_data.get("0th", {})
            dt_bytes = ifd0.get(piexif.ImageIFD.DateTime)
        if dt_bytes:
            try:
                timestamp = datetime.strptime(dt_bytes.decode(), "%Y:%m:%d %H:%M:%S")
            except (ValueError, UnicodeDecodeError):
                pass

        gps_ifd = exif_data.get("GPS", {})
        if gps_ifd:
            lat_tag = gps_ifd.get(piexif.GPSIFD.GPSLatitude)
            lat_ref = gps_ifd.get(piexif.GPSIFD.GPSLatitudeRef)
            lon_tag = gps_ifd.get(piexif.GPSIFD.GPSLongitude)
            lon_ref = gps_ifd.get(piexif.GPSIFD.GPSLongitudeRef)
            if lat_tag and lat_ref and lon_tag and lon_ref:
                try:
                    lat = _gps_to_decimal(lat_tag, lat_ref)
                    lon = _gps_to_decimal(lon_tag, lon_ref)
                except (ZeroDivisionError, TypeError, IndexError):
                    pass
    except Exception:
        # piexif can fail on non-JPEG or corrupt files; best-effort is fine
        pass

    return timestamp, lat, lon


_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _date_from_filename(path: Path) -> Optional[datetime]:
    """Return midnight datetime if the stem starts with YYYY-mm-dd, else None."""
    m = _DATE_PREFIX_RE.match(path.stem)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return None


def scan(source: Path, cache_dir: Optional[Path] = None) -> list[Photo]:
    """Walk source recursively, extract EXIF, return Photo list (SQLite-cached)."""
    if cache_dir is None:
        cache_dir = source

    db_path = cache_dir / CACHE_FILENAME
    conn = _init_cache(db_path)

    image_paths: list[Path] = []
    for root, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for fname in sorted(files):
            if Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS:
                image_paths.append(Path(root) / fname)

    photos: list[Photo] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Scanning photos..."),
        BarColumn(),
        TaskProgressColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task("scan", total=len(image_paths))

        for path in image_paths:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                progress.advance(task)
                continue

            row = conn.execute(
                "SELECT mtime, timestamp, lat, lon FROM scan_cache WHERE path = ?",
                (str(path),),
            ).fetchone()

            if row and abs(row[0] - mtime) < 0.001:
                ts_str, cached_lat, cached_lon = row[1], row[2], row[3]
                photo_ts = datetime.fromisoformat(ts_str) if ts_str else None
            else:
                photo_ts, cached_lat, cached_lon = _extract_exif(path)
                if photo_ts is None:
                    photo_ts = _date_from_filename(path)
                ts_str = photo_ts.isoformat() if photo_ts else None
                conn.execute(
                    "INSERT OR REPLACE INTO scan_cache (path, mtime, timestamp, lat, lon) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (str(path), mtime, ts_str, cached_lat, cached_lon),
                )
                conn.commit()

            photos.append(Photo(path=path, mtime=mtime, timestamp=photo_ts, lat=cached_lat, lon=cached_lon))
            progress.advance(task)

    conn.close()
    return photos
