from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional


@dataclass
class Photo:
    path: Path
    mtime: float
    timestamp: Optional[datetime]
    lat: Optional[float]
    lon: Optional[float]

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def has_timestamp(self) -> bool:
        return self.timestamp is not None


ClusterAction = Literal["accept", "skip", "merge"]


@dataclass
class Cluster:
    id: int
    name: str
    photos: list[Photo] = field(default_factory=list)
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    locked: bool = False
    action: ClusterAction = "accept"
    merge_target_id: Optional[int] = None

    @property
    def photo_count(self) -> int:
        return len(self.photos)

    @property
    def date_range(self) -> tuple[Optional[datetime], Optional[datetime]]:
        timestamps = [p.timestamp for p in self.photos if p.timestamp is not None]
        if not timestamps:
            return None, None
        return min(timestamps), max(timestamps)
