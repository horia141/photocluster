# photocluster

A CLI tool that scans a photo folder, clusters images by location and time, proposes human-readable album names, and organises them into directories — with an interactive review step.

**Platform:** macOS / Linux · **Python:** 3.11+

## Installation

```bash
# From the repo root
pip install -e .
```

Or in a virtual environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
photocluster --help
```

## Usage

```bash
# Basic usage — scan and organise in-place (mv mode)
photocluster ~/Dropbox/Photos

# Safe first run: dry-run to see what would happen
photocluster ~/Dropbox/Photos --dry-run

# Copy to a separate output folder (leaves Dropbox untouched)
photocluster ~/Dropbox/Photos --output ~/Photos/Organised --mode cp

# Tune the clustering sensitivity
photocluster ~/Dropbox/Photos --output ~/Photos/Organised --time-gap 3d --radius-km 100

# Output as JSON (no TUI, no file changes)
photocluster ~/Dropbox/Photos --json

# Undo the last mv run (pass the output dir, or source dir for in-place)
photocluster undo ~/Photos/Organised
```

## Typical workflow

1. Run with `--dry-run` first to see the proposed clusters in a rich table.
2. Run for real — a Textual TUI opens, listing all clusters with proposed names.
3. In the TUI:
   - `r` — rename a cluster inline
   - `k` — toggle skip / accept
   - `m` — merge two clusters
   - `s` — split a cluster at a date boundary
   - `g` / Enter — apply the plan
   - `q` — quit without changing anything
4. If you used `--mode mv`, an undo log path is printed at the end. Run `photocluster undo <dir>` to reverse it.

## Options

| Flag | Default | Description |
|---|---|---|
| `--output`, `-o` | *(in-place)* | Destination folder. Required for `cp` and `ln` modes. |
| `--mode`, `-m` | `mv` | File operation: `mv` (move), `cp` (copy), `ln` (symlink). |
| `--dry-run` | off | Preview proposed clusters without touching any files. |
| `--time-gap` | `48h` | Minimum time gap that starts a new cluster. Accepts `48h` or `3d`. |
| `--radius-km` | `50` | Maximum spatial radius for a single cluster (km). |
| `--json` | off | Print the proposed plan as JSON and exit. |
| `--algo` | `default` | Clustering algorithm: `default` (time gap + radius) or `dbscan`. |
| `--symlink-type` | `rel` | Symlink style for `ln` mode: `rel` (relative) or `abs` (absolute). |

## How it works

**Phase 1 — Scan:** Walks the source folder recursively, extracting EXIF timestamps and GPS coordinates from every image. Results are cached in a local SQLite file (`.photocluster_cache.db`) so unchanged files are skipped on subsequent runs.

**Phase 2 — Cluster:** Photos are sorted chronologically. A new cluster begins when the gap to the previous photo exceeds `--time-gap`. Within each time cluster, photos more than `--radius-km` from the group's centroid are split into a sub-cluster. Cluster centroids are reverse-geocoded via the Nominatim API (free, no key required, rate-limited to 1 req/s) to produce names like `2024.07.14 – Dubrovnik`. Photos without GPS fall back to `2024.07.14 – Untitled`.

**Phase 3 — Review and apply:** The interactive TUI lets you rename, merge, split, or skip clusters before anything is written. Existing subfolders are treated as locked clusters and default to skip, so hand-curated folders are never silently overwritten.

## File operation modes

| Mode | Behaviour | Requires `--output` |
|---|---|---|
| `mv` | Move files into cluster folders. Writes an undo log. | No (works in-place) |
| `cp` | Copy files, leaving originals untouched. | Yes |
| `ln` | Create symlinks pointing back to originals. | Yes |

`ln` mode warns if source and output are on different filesystems. Symlinks are relative by default; use `--symlink-type abs` to override.

## Safety

- `mv` mode writes `.photocluster_undo.json` alongside the output before touching any file. Run `photocluster undo <dir>` to reverse it.
- `cp` and `ln` modes never modify the source folder, so no undo is needed.
- EXIF data is read-only — photocluster never writes metadata back to image files.
