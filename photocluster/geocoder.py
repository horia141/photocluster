from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeRemainingColumn

from .models import Cluster

_geolocator = Nominatim(user_agent="photocluster/0.1.0")
_last_request: float = 0.0
_GEO_PRECISION = 3  # decimal places (~111 m) — coarse enough to share hits across nearby clusters


def _cache_key(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, _GEO_PRECISION), round(lon, _GEO_PRECISION)


def _reverse_geocode(lat: float, lon: float) -> Optional[str]:
    global _last_request
    elapsed = time.monotonic() - _last_request
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    try:
        location = _geolocator.reverse((lat, lon), exactly_one=True, timeout=10)
        _last_request = time.monotonic()
        if location is None:
            return None
        addr = location.raw.get("address", {})
        return (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("suburb")
            or addr.get("county")
            or addr.get("state")
            or addr.get("country")
        )
    except (GeocoderTimedOut, GeocoderServiceError):
        _last_request = time.monotonic()
        return None


def name_clusters(clusters: list[Cluster], cache_db: Optional[Path] = None) -> None:
    """Assign proposed names to unlocked unnamed clusters (in-place)."""
    to_geocode = [c for c in clusters if not c.locked and not c.name and c.centroid_lat is not None]

    conn: Optional[sqlite3.Connection] = None
    if cache_db is not None:
        conn = sqlite3.connect(cache_db)

    def _lookup(lat: float, lon: float) -> Optional[str]:
        if conn is None:
            return None
        clat, clon = _cache_key(lat, lon)
        row = conn.execute(
            "SELECT name FROM geocode_cache WHERE lat = ? AND lon = ?", (clat, clon)
        ).fetchone()
        return row[0] if row is not None else None

    def _store(lat: float, lon: float, name: Optional[str]) -> None:
        if conn is None:
            return
        clat, clon = _cache_key(lat, lon)
        conn.execute(
            "INSERT OR REPLACE INTO geocode_cache (lat, lon, name) VALUES (?, ?, ?)",
            (clat, clon, name),
        )
        conn.commit()

    # Split into cache hits and actual network requests
    cache_hits = sum(1 for c in to_geocode if _lookup(c.centroid_lat, c.centroid_lon) is not None)  # type: ignore[arg-type]
    need_fetch = len(to_geocode) - cache_hits

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Geocoding..."),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[dim]{task.fields[current]}"),
        TimeRemainingColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task("geocode", total=len(to_geocode), current="")

        for cluster in to_geocode:
            start, _ = cluster.date_range
            date_prefix = start.strftime("%Y.%m.%d") if start else "YYYY.MM.DD"
            progress.update(task, current=date_prefix)

            lat, lon = cluster.centroid_lat, cluster.centroid_lon  # type: ignore[assignment]
            city = _lookup(lat, lon)
            if city is None:
                city = _reverse_geocode(lat, lon)
                _store(lat, lon, city)

            cluster.name = f"{date_prefix} \u2013 {city}" if city else f"{date_prefix} \u2013 Untitled"
            progress.advance(task)

    if conn is not None:
        conn.close()

    # Handle clusters with no GPS
    for cluster in clusters:
        if not cluster.locked and not cluster.name:
            start, _ = cluster.date_range
            date_prefix = start.strftime("%Y.%m.%d") if start else "YYYY.MM.DD"
            cluster.name = f"{date_prefix} \u2013 Untitled"
