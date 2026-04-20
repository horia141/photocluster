from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from rich.console import Console
from rich.progress import track

from .models import Cluster

FileMode = Literal["mv", "cp", "ln"]
_console = Console()

UNDO_LOG_NAME = ".photocluster_undo.json"


def _undo_log_path(directory: Path) -> Path:
    return directory / UNDO_LOG_NAME


def _same_filesystem(p1: Path, p2: Path) -> bool:
    try:
        return os.stat(p1).st_dev == os.stat(p2).st_dev
    except OSError:
        return True


def _safe_dirname(name: str) -> str:
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    i = 1
    while True:
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def apply_plan(
    clusters: list[Cluster],
    source: Path,
    output: Path,
    mode: FileMode,
    symlink_type: Literal["rel", "abs"] = "rel",
    dry_run: bool = False,
) -> Optional[Path]:
    """Execute the confirmed plan. Returns undo log path for mv mode, else None."""
    if mode == "ln" and not _same_filesystem(source, output):
        _console.print(
            "[yellow]Warning:[/yellow] Source and output are on different filesystems. "
            "Symlinks may behave unexpectedly."
        )

    if not dry_run:
        output.mkdir(parents=True, exist_ok=True)

    undo_ops: list[dict] = []

    for cluster in clusters:
        if cluster.action == "skip":
            continue

        dest_dir = output / _safe_dirname(cluster.name)

        if dry_run:
            _console.print(f"  [dim]mkdir[/dim] {dest_dir}")
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)

        for photo in cluster.photos:
            dest_file = _unique_path(dest_dir / photo.path.name)

            if dry_run:
                _console.print(f"  [dim]{mode}[/dim]  {photo.path}  →  {dest_file}")
                continue

            if mode == "mv":
                undo_ops.append({"op": "mv", "src": str(dest_file), "dst": str(photo.path)})
                shutil.move(str(photo.path), dest_file)
            elif mode == "cp":
                shutil.copy2(str(photo.path), dest_file)
            elif mode == "ln":
                target = (
                    os.path.relpath(photo.path, dest_dir)
                    if symlink_type == "rel"
                    else str(photo.path.resolve())
                )
                dest_file.symlink_to(target)

    if mode == "mv" and not dry_run and undo_ops:
        undo_log = _undo_log_path(output)
        undo_log.write_text(
            json.dumps(
                {
                    "timestamp": datetime.now().isoformat(),
                    "source": str(source),
                    "output": str(output),
                    "operations": undo_ops,
                },
                indent=2,
            )
        )
        return undo_log

    return None


def undo_last_run(directory: Path) -> None:
    """Reverse the last mv run using the undo log in directory."""
    undo_log = _undo_log_path(directory)
    if not undo_log.exists():
        _console.print(f"[red]No undo log found at {undo_log}[/red]")
        return

    data = json.loads(undo_log.read_text())
    ops = data.get("operations", [])
    _console.print(f"[bold]Undoing {len(ops)} file operation(s)...[/bold]")

    for op in track(ops, description="Undoing..."):
        if op["op"] == "mv":
            src, dst = Path(op["src"]), Path(op["dst"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.move(str(src), dst)

    undo_log.unlink()
    _console.print("[green]Undo complete.[/green]")
