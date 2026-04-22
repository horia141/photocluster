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

# Inspect raw EXIF and GPS data for a single file
photocluster debug-exif ~/Photos/IMG_1234.jpg

# Clear the scan and geocode cache for a folder
photocluster clear-cache ~/Dropbox/Photos
```

## Typical workflow

1. Run with `--dry-run` first to see the proposed clusters in a rich table.
2. Run for real — a Textual TUI opens with three columns: clusters · files in cluster · image preview.
3. Use `left`/`right` to switch focus between the cluster list and the file list. Navigate with `up`/`down`.
4. Review and edit clusters using the keybindings below, then press `g` (or Enter) to apply.
5. If you used `--mode mv`, an undo log path is printed at the end. Run `photocluster undo <dir>` to reverse it.

## TUI keybindings

### Cluster operations

| Key | Action |
|-----|--------|
| `r` | Rename the current cluster |
| `k` | Toggle skip / accept |
| `m` | Merge into another cluster |

### Navigation within a cluster

| Key | Action |
|-----|--------|
| `e` | Jump to the earliest photo |
| `b` | Jump to the middle photo (bisect) |
| `l` | Jump to the latest photo |
| `n` | Jump to the first photo of the next day |
| `o` | Open the current photo in the system viewer |
| `f` | Open the cluster folder in Finder / file manager |

### Selection and editing

Select photos within a cluster to extract or move them as a group.

| Key | Action |
|-----|--------|
| `s` | Toggle selection on the current photo |
| `d` | Extend selection as a range from the nearest selected photo to the cursor |
| `c` | Cancel / clear the selection |
| `x` | Extract selection into a new cluster (prompts for a name) |
| `a` | Move selection into an existing cluster |
| `y` | Yank (remove) the current photo or selection from the cluster entirely. If the cluster becomes empty it is removed. |
| `;` | Send the current photo or selection to the **Random** cluster — a catch-all for images that don't belong anywhere specific. The cluster is created on first use and is always sorted to the bottom. |

### Apply / quit

| Key | Action |
|-----|--------|
| `g` / Enter | Apply the plan and organise files |
| `q` | Quit without changing anything |

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

**Phase 1 — Scan:** Walks the source folder recursively, extracting EXIF timestamps and GPS coordinates from every image. If no EXIF timestamp is found, the filename is checked for a `YYYY-MM-DD` prefix as a fallback. Results are cached in a local SQLite file (`.photocluster_cache.db`) so unchanged files are skipped on subsequent runs.

**Phase 2 — Cluster:** Photos are sorted chronologically. A new cluster begins when the gap to the previous photo exceeds `--time-gap`. Within each time cluster, photos more than `--radius-km` from the group's centroid are split into a sub-cluster. Every cluster centroid with GPS is reverse-geocoded via the Nominatim API (free, no key required, rate-limited to 1 req/s) to produce names like `2024.07.14 – Dubrovnik`. Geocoding results are cached in the same SQLite database. On subsequent runs, locked clusters (existing subfolders) are also geocoded into the cache so the TUI can show location names regardless of whether individual photos carry GPS data. Photos without GPS fall back to `2024.07.14 – Untitled`.

**Phase 3 — Review and apply:** The interactive TUI shows clusters, files, and a live image preview side by side. The cluster list is always sorted by date, with the Random catch-all cluster pinned at the bottom. Existing subfolders are treated as locked clusters and default to skip, so hand-curated folders are never silently overwritten.

## TUI layout

```
┌─────────────────────┬────────────────┬──────────────────┐
│  Clusters (2fr)     │  Files (1fr)   │  Preview (1fr)   │
│                     │                │                  │
│  2024.07.14 –       │  # File         │  [image]         │
│  Dubrovnik          │  1 IMG_001.jpg │                  │
│  2024.08.03 –       │  2 IMG_002.jpg │                  │
│  Paris              │  …             │                  │
│  …                  │                │                  │
│  Random             │                │                  │
└─────────────────────┴────────────────┴──────────────────┘
```

The file list shows each photo's position, filename, timestamp, and geocoded location (inferred from the cluster centroid, so it appears for all photos in the cluster regardless of individual GPS data).

## File operation modes

| Mode | Behaviour | Requires `--output` |
|---|---|---|
| `mv` | Move files into cluster folders. Writes an undo log. | No (works in-place) |
| `cp` | Copy files, leaving originals untouched. | Yes |
| `ln` | Create symlinks pointing back to originals. | Yes |

`ln` mode warns if source and output are on different filesystems. Symlinks are relative by default; use `--symlink-type abs` to override.

## Subcommands

| Command | Description |
|---------|-------------|
| `photocluster <source>` | Main command — scan, cluster, review, and organise. |
| `photocluster undo <dir>` | Reverse the last `mv` run using the undo log in `<dir>`. |
| `photocluster debug-exif <file>` | Print raw EXIF and GPS tags from a single image for debugging. |
| `photocluster clear-cache <source>` | Delete the `.photocluster_cache.db` scan and geocode cache for `<source>`. |

## Safety

- `mv` mode writes `.photocluster_undo.json` alongside the output before touching any file. Run `photocluster undo <dir>` to reverse it.
- `cp` and `ln` modes never modify the source folder, so no undo is needed.
- EXIF data is read-only — photocluster never writes metadata back to image files.
- Locked clusters (existing subfolders) default to skip — they are never silently overwritten.
