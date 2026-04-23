from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import Cluster, Photo

DRAFT_FILENAME = ".photocluster_draft.json"
DRAFT_VERSION = 1


def draft_path(source: Path) -> Path:
    return source / DRAFT_FILENAME


def save_draft(clusters: list[Cluster], path: Path) -> None:
    data = {
        "version": DRAFT_VERSION,
        "clusters": [
            {
                "id": c.id,
                "name": c.name,
                "locked": c.locked,
                "action": c.action,
                "merge_target_id": c.merge_target_id,
                "photo_paths": [str(p.path) for p in c.photos],
            }
            for c in clusters
        ],
    }
    path.write_text(json.dumps(data, indent=2))


def load_draft(
    clusters: list[Cluster],
    photos: list[Photo],
    path: Path,
) -> tuple[list[Cluster], int, int]:
    """Reconstruct clusters from a saved draft.

    Returns (restored_clusters, n_matched, n_missing) where n_missing is the
    count of photo paths in the draft that no longer exist on disk.
    """
    from .clusterer import _centroid

    data = json.loads(path.read_text())
    if data.get("version") != DRAFT_VERSION:
        return clusters, 0, 0

    photo_map: dict[str, Photo] = {str(p.path): p for p in photos}
    draft_seen: set[str] = set()

    result: list[Cluster] = []
    n_missing = 0

    for c_data in data["clusters"]:
        matched: list[Photo] = []
        for p_str in c_data["photo_paths"]:
            draft_seen.add(p_str)
            if p_str in photo_map:
                matched.append(photo_map[p_str])
            else:
                n_missing += 1

        # Keep empty named clusters (e.g. Random) so they survive a round-trip
        clat, clon = _centroid(matched) if matched else (None, None)
        result.append(Cluster(
            id=c_data["id"],
            name=c_data["name"],
            photos=matched,
            centroid_lat=clat,
            centroid_lon=clon,
            locked=c_data["locked"],
            action=c_data["action"],
            merge_target_id=c_data.get("merge_target_id"),
        ))

    # Photos that were added to the source folder after the draft was saved
    # are simply left out; the user can clear the draft to start fresh.
    n_matched = len([p for p in photos if str(p.path) in draft_seen])
    return result, n_matched, n_missing
