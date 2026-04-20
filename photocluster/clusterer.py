from __future__ import annotations

import re
from datetime import timedelta
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Optional

from .models import Cluster, Photo


def parse_time_gap(s: str) -> timedelta:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(h|d)", s.lower())
    if not m:
        raise ValueError(f"Invalid time gap '{s}'. Use format like '48h' or '3d'.")
    value, unit = float(m.group(1)), m.group(2)
    return timedelta(hours=value) if unit == "h" else timedelta(days=value)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * asin(sqrt(max(0.0, min(1.0, a))))


def _centroid(photos: list[Photo]) -> tuple[Optional[float], Optional[float]]:
    gps = [p for p in photos if p.has_gps]
    if not gps:
        return None, None
    return sum(p.lat for p in gps) / len(gps), sum(p.lon for p in gps) / len(gps)  # type: ignore[operator]


def _seed_point(photos: list[Photo]) -> tuple[Optional[float], Optional[float]]:
    """Return coords of the GPS photo closest to the group's mean — avoids landing between clusters."""
    gps = [p for p in photos if p.has_gps]
    if not gps:
        return None, None
    mean_lat = sum(p.lat for p in gps) / len(gps)  # type: ignore[operator]
    mean_lon = sum(p.lon for p in gps) / len(gps)  # type: ignore[operator]
    seed = min(gps, key=lambda p: (p.lat - mean_lat) ** 2 + (p.lon - mean_lon) ** 2)  # type: ignore[operator]
    return seed.lat, seed.lon


def _split_by_radius(photos: list[Photo], radius_km: float) -> list[list[Photo]]:
    if not photos:
        return []

    remaining = list(photos)
    groups: list[list[Photo]] = []

    for _ in range(50):
        if not remaining:
            break

        lat, lon = _seed_point(remaining)
        if lat is None:
            # No GPS — can't split spatially; keep together
            groups.append(remaining)
            remaining = []
            break

        core = [p for p in remaining if not p.has_gps or haversine_km(lat, lon, p.lat, p.lon) <= radius_km]  # type: ignore[arg-type]
        outliers = [p for p in remaining if p.has_gps and haversine_km(lat, lon, p.lat, p.lon) > radius_km]  # type: ignore[arg-type]

        if not core:
            groups.append(remaining)
            remaining = []
            break

        groups.append(core)
        remaining = outliers

    if remaining:
        groups.append(remaining)

    return [g for g in groups if g]


def _default_cluster(photos: list[Photo], time_gap: timedelta, radius_km: float) -> list[list[Photo]]:
    if not photos:
        return []

    time_groups: list[list[Photo]] = [[photos[0]]]
    for photo in photos[1:]:
        prev = time_groups[-1][-1]
        assert prev.timestamp is not None and photo.timestamp is not None
        if photo.timestamp - prev.timestamp > time_gap:
            time_groups.append([photo])
        else:
            time_groups[-1].append(photo)

    result: list[list[Photo]] = []
    for group in time_groups:
        result.extend(_split_by_radius(group, radius_km))
    return result


def _dbscan_cluster(photos: list[Photo], time_gap: timedelta, radius_km: float) -> list[list[Photo]]:
    try:
        import numpy as np
        from sklearn.cluster import DBSCAN
    except ImportError:
        raise ImportError("scikit-learn is required for --algo dbscan. Run: pip install scikit-learn")

    if not photos:
        return []

    time_scale = max(time_gap.total_seconds() / 3600, 1.0)
    space_scale = max(radius_km / 111.0, 0.001)

    features = []
    for p in photos:
        t = p.timestamp.timestamp() / 3600  # type: ignore[union-attr]
        plat = p.lat if p.lat is not None else 0.0
        plon = p.lon if p.lon is not None else 0.0
        features.append([t / time_scale, plat / space_scale, plon / space_scale])

    X = np.array(features)
    labels = DBSCAN(eps=1.0, min_samples=1).fit(X).labels_

    groups: dict[int, list[Photo]] = {}
    for i, label in enumerate(labels):
        groups.setdefault(int(label), []).append(photos[i])
    return list(groups.values())


def cluster_photos(
    photos: list[Photo],
    source: Path,
    time_gap: timedelta,
    radius_km: float,
    algo: str = "default",
) -> list[Cluster]:
    """Return clusters, with locked clusters for existing subdirectories first."""
    locked_map: dict[Path, list[Photo]] = {}
    root_photos: list[Photo] = []

    for photo in photos:
        try:
            rel = photo.path.relative_to(source)
        except ValueError:
            root_photos.append(photo)
            continue
        if len(rel.parts) > 1:
            locked_map.setdefault(source / rel.parts[0], []).append(photo)
        else:
            root_photos.append(photo)

    clusters: list[Cluster] = []
    cluster_id = 0

    for folder in sorted(locked_map):
        folder_photos = locked_map[folder]
        clat, clon = _centroid(folder_photos)
        clusters.append(Cluster(
            id=cluster_id,
            name=folder.name,
            photos=folder_photos,
            centroid_lat=clat,
            centroid_lon=clon,
            locked=True,
            action="skip",
        ))
        cluster_id += 1

    timed = sorted([p for p in root_photos if p.has_timestamp], key=lambda p: p.timestamp)  # type: ignore[arg-type, return-value]
    untimed = [p for p in root_photos if not p.has_timestamp]

    groups = _dbscan_cluster(timed, time_gap, radius_km) if algo == "dbscan" else _default_cluster(timed, time_gap, radius_km)

    for group in groups:
        clat, clon = _centroid(group)
        clusters.append(Cluster(
            id=cluster_id,
            name="",
            photos=group,
            centroid_lat=clat,
            centroid_lon=clon,
            locked=False,
            action="accept",
        ))
        cluster_id += 1

    if untimed:
        clat, clon = _centroid(untimed)
        clusters.append(Cluster(
            id=cluster_id,
            name="Untitled",
            photos=untimed,
            centroid_lat=clat,
            centroid_lon=clon,
            locked=False,
            action="accept",
        ))

    return clusters
