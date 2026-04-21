from __future__ import annotations

import time
from typing import Optional

from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeRemainingColumn

from .models import Cluster

_geolocator = Nominatim(user_agent="photocluster/0.1.0")
_last_request: float = 0.0


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


def name_clusters(clusters: list[Cluster]) -> None:
    """Assign proposed names to unlocked unnamed clusters (in-place)."""
    to_geocode = [c for c in clusters if not c.locked and not c.name and c.centroid_lat is not None]

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
            city = _reverse_geocode(cluster.centroid_lat, cluster.centroid_lon)  # type: ignore[arg-type]
            cluster.name = f"{date_prefix} \u2013 {city}" if city else f"{date_prefix} \u2013 Untitled"
            progress.advance(task)

    # Handle clusters with no GPS
    for cluster in clusters:
        if not cluster.locked and not cluster.name:
            start, _ = cluster.date_range
            date_prefix = start.strftime("%Y.%m.%d") if start else "YYYY.MM.DD"
            cluster.name = f"{date_prefix} \u2013 Untitled"
