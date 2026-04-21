from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image as _PilImage
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from .models import Photo

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".tiff", ".tif", ".heic", ".png", ".webp"}
CACHE_FILENAME = ".photocluster_cache.db"
CACHE_VERSION = "3"  # bump to invalidate stale entries after extraction changes


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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geocode_cache (
            lat  REAL NOT NULL,
            lon  REAL NOT NULL,
            name TEXT,
            PRIMARY KEY (lat, lon)
        )
    """)
    conn.commit()
    row = conn.execute("SELECT value FROM cache_meta WHERE key = 'version'").fetchone()
    if row is None or row[0] != CACHE_VERSION:
        conn.execute("DELETE FROM scan_cache")
        conn.execute(
            "INSERT OR REPLACE INTO cache_meta (key, value) VALUES ('version', ?)",
            (CACHE_VERSION,),
        )
        conn.commit()
    return conn


def _gps_to_decimal(coords: tuple, ref: bytes | str) -> float:
    d, m, s = (float(v) for v in coords)
    decimal = d + m / 60 + s / 3600
    if ref in (b"S", b"W", "S", "W"):
        decimal = -decimal
    return decimal


_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _date_from_filename(path: Path) -> Optional[datetime]:
    m = _DATE_PREFIX_RE.match(path.stem)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            pass
    return None


def _extract_exif(path: Path) -> tuple[Optional[datetime], Optional[float], Optional[float]]:
    timestamp: Optional[datetime] = None
    lat: Optional[float] = None
    lon: Optional[float] = None

    try:
        img = _PilImage.open(path)
        exif = img.getexif()

        # DateTimeOriginal (0x9003) preferred over DateTime (0x0132)
        dt_str = exif.get(0x9003) or exif.get(0x0132)
        if dt_str:
            try:
                timestamp = datetime.strptime(str(dt_str), "%Y:%m:%d %H:%M:%S")
            except ValueError:
                pass

        gps_ifd = exif.get_ifd(0x8825)
        if gps_ifd:
            lat_coords = gps_ifd.get(2)   # GPSLatitude
            lat_ref    = gps_ifd.get(1)   # GPSLatitudeRef
            lon_coords = gps_ifd.get(4)   # GPSLongitude
            lon_ref    = gps_ifd.get(3)   # GPSLongitudeRef
            if lat_coords and lat_ref and lon_coords and lon_ref:
                try:
                    lat = _gps_to_decimal(lat_coords, lat_ref)
                    lon = _gps_to_decimal(lon_coords, lon_ref)
                except (ZeroDivisionError, TypeError, IndexError, ValueError):
                    pass
    except Exception:
        pass

    return timestamp, lat, lon


def cache_db_path(source: Path, cache_dir: Optional[Path] = None) -> Path:
    return (cache_dir if cache_dir is not None else source) / CACHE_FILENAME


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
                ts_str = photo_ts.isoformat() if photo_ts else None
                conn.execute(
                    "INSERT OR REPLACE INTO scan_cache (path, mtime, timestamp, lat, lon) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (str(path), mtime, ts_str, cached_lat, cached_lon),
                )
                conn.commit()

            if photo_ts is None:
                photo_ts = _date_from_filename(path)

            photos.append(Photo(path=path, mtime=mtime, timestamp=photo_ts, lat=cached_lat, lon=cached_lon))
            progress.advance(task)

    conn.close()
    return photos
