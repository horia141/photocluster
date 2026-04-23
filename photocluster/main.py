from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .clusterer import cluster_photos, parse_time_gap
from .draft import draft_path, load_draft, save_draft
from .executor import FileMode, apply_plan, undo_last_run
from .geocoder import name_clusters
from .scanner import cache_db_path, scan

app = typer.Typer(
    name="photocluster",
    help="Scan, cluster, and organise photos by location and time.",
    no_args_is_help=True,
)
console = Console()


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


@app.command()
def main(
    source: Path = typer.Argument(..., help="Source photo folder to scan."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Destination folder (required for cp/ln modes)."),
    mode: str = typer.Option("mv", "--mode", "-m", help="File operation: mv | cp | ln."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview proposed clusters without touching files."),
    time_gap: str = typer.Option("48h", "--time-gap", help="Gap between clusters, e.g. 48h or 3d."),
    radius_km: float = typer.Option(50.0, "--radius-km", help="Max spatial radius per cluster (km)."),
    json_output: bool = typer.Option(False, "--json", help="Print proposed plan as JSON and exit."),
    algo: str = typer.Option("default", "--algo", help="Clustering algorithm: default | dbscan."),
    symlink_type: str = typer.Option("rel", "--symlink-type", help="Symlink style for ln mode: rel | abs."),
) -> None:
    """Scan SOURCE, cluster photos, review interactively, and organise into folders."""

    # --- Validate args -------------------------------------------------------
    if mode not in ("mv", "cp", "ln"):
        console.print(f"[red]Error:[/red] --mode must be mv, cp, or ln. Got '{mode}'.")
        raise typer.Exit(1)
    if symlink_type not in ("rel", "abs"):
        console.print(f"[red]Error:[/red] --symlink-type must be rel or abs. Got '{symlink_type}'.")
        raise typer.Exit(1)
    if algo not in ("default", "dbscan"):
        console.print(f"[red]Error:[/red] --algo must be default or dbscan. Got '{algo}'.")
        raise typer.Exit(1)
    if mode in ("cp", "ln") and output is None:
        console.print(
            f"[red]Error:[/red] --mode {mode} requires an explicit --output destination.\n"
            "In-place mode (no --output) is only supported with --mode mv."
        )
        raise typer.Exit(1)

    if not source.is_dir():
        console.print(f"[red]Error:[/red] Source path does not exist or is not a directory: {source}")
        raise typer.Exit(1)

    try:
        gap = parse_time_gap(time_gap)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    effective_output = output if output is not None else source

    # --- Phase 1: Scan -------------------------------------------------------
    console.rule("[bold blue]Phase 1 — Scan")
    photos = scan(source)
    console.print(f"Found [bold]{len(photos)}[/bold] image(s) in {source}")

    if not photos:
        console.print("[yellow]No images found. Nothing to do.[/yellow]")
        raise typer.Exit(0)

    # --- Phase 2: Cluster ----------------------------------------------------
    console.rule("[bold blue]Phase 2 — Cluster")
    clusters = cluster_photos(photos, source=source, time_gap=gap, radius_km=radius_km, algo=algo)
    console.print(
        f"Produced [bold]{len(clusters)}[/bold] cluster(s) "
        f"({sum(1 for c in clusters if c.locked)} locked, "
        f"{sum(1 for c in clusters if not c.locked)} new)"
    )

    name_clusters(clusters, cache_db=cache_db_path(source))

    # --- Resume from draft ---------------------------------------------------
    dp = draft_path(source)
    if dp.exists():
        clusters, n_matched, n_missing = load_draft(clusters, photos, dp)
        msg = f"[bold yellow]Resuming from draft[/bold yellow] ({n_matched} photo(s) restored"
        if n_missing:
            msg += f", {n_missing} no longer on disk and skipped"
        console.print(msg + ")")

    # --- JSON output ---------------------------------------------------------
    if json_output:
        data = {
            "source": str(source),
            "output": str(effective_output),
            "mode": mode,
            "clusters": [
                {
                    "id": c.id,
                    "name": c.name,
                    "photo_count": c.photo_count,
                    "start_date": c.date_range[0].isoformat() if c.date_range[0] else None,
                    "end_date": c.date_range[1].isoformat() if c.date_range[1] else None,
                    "centroid_lat": c.centroid_lat,
                    "centroid_lon": c.centroid_lon,
                    "locked": c.locked,
                    "action": c.action,
                }
                for c in clusters
            ],
        }
        print(json.dumps(data, indent=2))
        raise typer.Exit(0)

    # --- Dry run -------------------------------------------------------------
    if dry_run:
        console.rule("[bold blue]Dry Run — Proposed Plan")
        _print_cluster_table(clusters, effective_output, mode)
        console.print("\n[dim]Dry run complete — no files were touched.[/dim]")
        raise typer.Exit(0)

    # --- Phase 3: Interactive review -----------------------------------------
    console.rule("[bold blue]Phase 3 — Review")
    from .tui import ClusterReviewApp

    tui_app = ClusterReviewApp(
        clusters=clusters,
        mode=mode,
        output=str(effective_output),
        cache_db=cache_db_path(source),
        draft_path=dp,
    )
    result = tui_app.run()

    if not result:
        console.print("[yellow]Aborted — no files were modified.[/yellow]")
        raise typer.Exit(0)

    clusters = result

    # --- Apply ---------------------------------------------------------------
    if dp.exists():
        dp.unlink()
    console.rule("[bold blue]Applying plan")
    undo_log = apply_plan(
        clusters,
        source=source,
        output=effective_output,
        mode=mode,  # type: ignore[arg-type]
        symlink_type=symlink_type,  # type: ignore[arg-type]
        dry_run=False,
    )

    accepted = sum(1 for c in clusters if c.action == "accept")
    skipped = sum(1 for c in clusters if c.action == "skip")
    console.print(f"[green]Done.[/green] Applied {accepted} cluster(s), skipped {skipped}.")

    if undo_log:
        console.print(f"Undo log written to: [bold]{undo_log}[/bold]")
        console.print("To undo: [bold]photocluster undo {effective_output}[/bold]")


# ---------------------------------------------------------------------------
# Debug command
# ---------------------------------------------------------------------------


@app.command(name="debug-exif")
def debug_exif(
    file: Path = typer.Argument(..., help="Image file to inspect."),
) -> None:
    """Print raw EXIF and GPS data extracted from a single image file."""
    from PIL import Image as _PilImage

    if not file.is_file():
        console.print(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    try:
        img = _PilImage.open(file)
        exif = img.getexif()
    except Exception as exc:
        console.print(f"[red]Failed to open/read EXIF:[/red] {exc}")
        raise typer.Exit(1)

    console.print(f"\n[bold]File:[/bold] {file}")
    console.print(f"[bold]Format:[/bold] {img.format}  [bold]Mode:[/bold] {img.mode}  [bold]Size:[/bold] {img.size}")

    dt_orig = exif.get(0x9003)
    dt_mod  = exif.get(0x0132)
    console.print(f"\n[bold]DateTimeOriginal (0x9003):[/bold] {dt_orig!r}")
    console.print(f"[bold]DateTime         (0x0132):[/bold] {dt_mod!r}")

    gps_ifd = exif.get_ifd(0x8825)
    console.print(f"\n[bold]GPS IFD (0x8825):[/bold] {dict(gps_ifd) if gps_ifd else 'empty / not found'}")

    if gps_ifd:
        lat_coords = gps_ifd.get(2)
        lat_ref    = gps_ifd.get(1)
        lon_coords = gps_ifd.get(4)
        lon_ref    = gps_ifd.get(3)
        console.print(f"  LatitudeRef={lat_ref!r}  Latitude={lat_coords!r}")
        console.print(f"  LongitudeRef={lon_ref!r}  Longitude={lon_coords!r}")
        if lat_coords and lat_ref and lon_coords and lon_ref:
            try:
                from .scanner import _gps_to_decimal
                lat = _gps_to_decimal(lat_coords, lat_ref)
                lon = _gps_to_decimal(lon_coords, lon_ref)
                console.print(f"\n[green]Parsed:[/green] lat={lat:.6f}  lon={lon:.6f}")
            except Exception as exc:
                console.print(f"\n[red]Parse failed:[/red] {exc}")
        else:
            console.print("\n[yellow]Incomplete GPS tags — cannot parse coordinates.[/yellow]")


# ---------------------------------------------------------------------------
# Clear-cache command
# ---------------------------------------------------------------------------


@app.command(name="clear-cache")
def clear_cache(
    source: Path = typer.Argument(..., help="Source photo folder whose cache should be cleared."),
) -> None:
    """Delete the scan and geocode cache for SOURCE."""
    db = cache_db_path(source)
    if not db.exists():
        console.print(f"[yellow]No cache found at {db}[/yellow]")
        raise typer.Exit(0)
    db.unlink()
    console.print(f"[green]Deleted cache:[/green] {db}")


# ---------------------------------------------------------------------------
# Undo command
# ---------------------------------------------------------------------------


@app.command()
def undo(
    directory: Path = typer.Argument(
        Path("."),
        help="Directory containing the undo log (defaults to current directory).",
    ),
) -> None:
    """Undo the last mv run using the undo log in DIRECTORY."""
    undo_last_run(directory.resolve())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_cluster_table(clusters: list, output: Path, mode: str) -> None:
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("#", width=4)
    table.add_column("Name")
    table.add_column("Photos", width=7, justify="right")
    table.add_column("Dates", width=24)
    table.add_column("Status", width=10)

    for c in clusters:
        start, end = c.date_range
        if start and end and start.date() != end.date():
            dates = f"{start.strftime('%Y-%m-%d')} → {end.strftime('%m-%d')}"
        elif start:
            dates = start.strftime("%Y-%m-%d")
        else:
            dates = "–"

        status = "[dim]skip[/dim]" if c.action == "skip" else "[green]accept[/green]"
        locked_tag = " [yellow](locked)[/yellow]" if c.locked else ""
        table.add_row(str(c.id), f"{c.name}{locked_tag}", str(c.photo_count), dates, status)

    console.print(table)
    console.print(f"\nMode: [bold]{mode}[/bold]   Output: [bold]{output}[/bold]")
