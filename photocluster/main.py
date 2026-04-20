from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .clusterer import cluster_photos, parse_time_gap
from .executor import FileMode, apply_plan, undo_last_run
from .geocoder import name_clusters
from .scanner import scan

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

    name_clusters(clusters)

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
    )
    result = tui_app.run()

    if not result:
        console.print("[yellow]Aborted — no files were modified.[/yellow]")
        raise typer.Exit(0)

    clusters = result

    # --- Apply ---------------------------------------------------------------
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
