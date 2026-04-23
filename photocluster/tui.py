from __future__ import annotations

import copy
from datetime import datetime
from typing import Literal, Optional

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from .models import Cluster, Photo


# ---------------------------------------------------------------------------
# Modal dialogs
# ---------------------------------------------------------------------------


class RenameDialog(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    RenameDialog {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #dialog Label { margin-bottom: 1; }
    #dialog Input { margin-bottom: 1; }
    #buttons { height: auto; }
    #buttons Button { margin-right: 1; }
    """

    def __init__(self, current_name: str) -> None:
        super().__init__()
        self._current = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Rename cluster:")
            yield Input(value=self._current, id="name-input")
            with Horizontal(id="buttons"):
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self.query_one("#name-input", Input).value.strip() or None)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class MergeDialog(ModalScreen[Optional[int]]):
    """Pick a target cluster to merge the current cluster into."""

    DEFAULT_CSS = """
    MergeDialog {
        align: center middle;
    }
    #dialog {
        width: 70;
        height: auto;
        max-height: 30;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #dialog Label { margin-bottom: 1; }
    #merge-table { height: 15; margin-bottom: 1; }
    #buttons Button { margin-right: 1; }
    """

    def __init__(self, clusters: list[Cluster], exclude_id: int) -> None:
        super().__init__()
        self._clusters = [c for c in clusters if c.id != exclude_id and c.action != "skip"]
        self._selected_id: Optional[int] = self._clusters[0].id if self._clusters else None

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Merge into which cluster?")
            table: DataTable = DataTable(id="merge-table", cursor_type="row")
            yield table
            with Horizontal(id="buttons"):
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        table = self.query_one("#merge-table", DataTable)
        table.add_column("ID", key="id", width=6)
        table.add_column("Name", key="name")
        table.add_column("Photos", key="photos", width=8)
        for c in self._clusters:
            table.add_row(str(c.id), c.name, str(c.photo_count), key=str(c.id))

    @on(DataTable.RowSelected, "#merge-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self._selected_id = int(str(event.row_key.value))

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self._selected_id)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class SplitDialog(ModalScreen[Optional[datetime]]):
    """Ask for a split date (YYYY-MM-DD)."""

    DEFAULT_CSS = """
    SplitDialog {
        align: center middle;
    }
    #dialog {
        width: 50;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #dialog Label { margin-bottom: 1; }
    #dialog Input { margin-bottom: 1; }
    #error { color: $error; height: 1; margin-bottom: 1; }
    #buttons Button { margin-right: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Split at date (YYYY-MM-DD).\nPhotos from this date onwards go to new cluster.")
            yield Input(placeholder="e.g. 2024-07-16", id="date-input")
            yield Label("", id="error")
            with Horizontal(id="buttons"):
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._try_submit()

    @on(Input.Submitted)
    def _submitted(self, _: Input.Submitted) -> None:
        self._try_submit()

    def _try_submit(self) -> None:
        raw = self.query_one("#date-input", Input).value.strip()
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d")
            self.dismiss(dt)
        except ValueError:
            self.query_one("#error", Label).update(f"Invalid date: '{raw}'")

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Main review app
# ---------------------------------------------------------------------------


class ClusterReviewApp(App[list[Cluster]]):
    """Interactive TUI for reviewing and editing proposed clusters."""

    TITLE = "photocluster"

    BINDINGS = [
        Binding("r", "rename", "Rename"),
        Binding("k", "toggle_skip", "Skip/Accept"),
        Binding("m", "merge", "Merge"),
        Binding("s", "split", "Split"),
        Binding("u", "undo", "Undo"),
        Binding("g", "go", "Go (apply)"),
        Binding("enter", "go", "Go (apply)", show=False),
        Binding("q", "quit_app", "Quit"),
    ]

    DEFAULT_CSS = """
    #info-bar {
        height: 3;
        background: $primary-darken-2;
        color: $text;
        padding: 1 2;
        border-bottom: solid $primary;
    }
    #cluster-table {
        height: 1fr;
    }
    """

    def __init__(
        self,
        clusters: list[Cluster],
        mode: str,
        output: str,
    ) -> None:
        super().__init__()
        self._clusters = list(clusters)
        self._mode = mode
        self._output = output
        self._undo_snapshot: Optional[list[Cluster]] = None

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            f"  Mode: [bold]{self._mode}[/bold]   "
            f"Destination: [bold]{self._output}[/bold]   "
            f"  [dim]r[/dim]:Rename  [dim]k[/dim]:Skip  [dim]m[/dim]:Merge  [dim]s[/dim]:Split  "
            f"[dim]u[/dim]:Undo  [dim]g/Enter[/dim]:Apply  [dim]q[/dim]:Quit",
            id="info-bar",
            markup=True,
        )
        yield DataTable(id="cluster-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self._build_table()

    # ------------------------------------------------------------------
    # Table helpers
    # ------------------------------------------------------------------

    def _build_table(self) -> None:
        table = self.query_one("#cluster-table", DataTable)
        table.clear(columns=True)
        table.add_column("#", key="id", width=5)
        table.add_column("Name", key="name")
        table.add_column("Photos", key="photos", width=8)
        table.add_column("Dates", key="dates", width=22)
        table.add_column("Status", key="status", width=18)
        table.add_column("Type", key="type", width=8)
        for c in self._clusters:
            table.add_row(*self._row_cells(c), key=str(c.id))

    def _row_cells(self, c: Cluster) -> tuple:
        start, end = c.date_range
        if start and end and start.date() != end.date():
            dates = f"{start.strftime('%Y-%m-%d')} → {end.strftime('%m-%d')}"
        elif start:
            dates = start.strftime("%Y-%m-%d")
        else:
            dates = "–"

        status_text = self._status_label(c)
        type_label = Text("locked", style="yellow") if c.locked else Text("new", style="green")

        return str(c.id), c.name, str(c.photo_count), dates, status_text, type_label

    def _status_label(self, c: Cluster) -> Text:
        if c.action == "skip":
            return Text("Skip", style="dim")
        if c.action == "merge" and c.merge_target_id is not None:
            target = self._cluster_by_id(c.merge_target_id)
            label = target.name[:14] + "…" if target and len(target.name) > 14 else (target.name if target else "?")
            return Text(f"Merge→ {label}", style="cyan")
        return Text("Accept", style="bold green")

    def _cluster_by_id(self, cid: int) -> Optional[Cluster]:
        return next((c for c in self._clusters if c.id == cid), None)

    def _current_cluster(self) -> Optional[Cluster]:
        table = self.query_one("#cluster-table", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return self._cluster_by_id(int(str(row_key.value)))

    def _refresh_row(self, cluster: Cluster) -> None:
        table = self.query_one("#cluster-table", DataTable)
        row_key = str(cluster.id)
        cells = self._row_cells(cluster)
        col_keys = ["id", "name", "photos", "dates", "status", "type"]
        for key, value in zip(col_keys, cells):
            table.update_cell(row_key, key, value, update_width=True)

    # ------------------------------------------------------------------
    # Undo helpers
    # ------------------------------------------------------------------

    def _save_undo(self) -> None:
        self._undo_snapshot = copy.deepcopy(self._clusters)

    def action_undo(self) -> None:
        if self._undo_snapshot is None:
            self.notify("Nothing to undo.", severity="warning")
            return
        self._clusters = self._undo_snapshot
        self._undo_snapshot = None
        self._build_table()
        self.notify("Undone.")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_rename(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return

        def _apply(new_name: Optional[str]) -> None:
            if new_name:
                cluster.name = new_name
                self._refresh_row(cluster)

        self._save_undo()
        self.push_screen(RenameDialog(cluster.name), _apply)

    def action_toggle_skip(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return
        self._save_undo()
        if cluster.action == "skip":
            cluster.action = "accept"
            cluster.merge_target_id = None
        else:
            cluster.action = "skip"
        self._refresh_row(cluster)

    def action_merge(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return
        if len([c for c in self._clusters if c.id != cluster.id]) == 0:
            self.notify("No other clusters to merge into.", severity="warning")
            return

        def _apply(target_id: Optional[int]) -> None:
            if target_id is not None:
                cluster.action = "merge"
                cluster.merge_target_id = target_id
                self._refresh_row(cluster)

        self._save_undo()
        self.push_screen(MergeDialog(self._clusters, exclude_id=cluster.id), _apply)

    def action_split(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return
        if cluster.locked:
            self.notify("Locked clusters cannot be split here.", severity="warning")
            return

        def _apply(split_date: Optional[datetime]) -> None:
            if split_date is None:
                return
            before = [p for p in cluster.photos if p.timestamp is None or p.timestamp < split_date]
            after = [p for p in cluster.photos if p.timestamp is not None and p.timestamp >= split_date]
            if not before or not after:
                self.notify("Split date produces an empty half — choose a different date.", severity="warning")
                return

            # Shorten the original cluster to 'before' photos
            cluster.photos = before
            cluster.name = cluster.name  # unchanged

            # Create the new cluster
            new_id = max(c.id for c in self._clusters) + 1
            from .clusterer import _centroid
            clat, clon = _centroid(after)
            start, _ = min(
                ((p.timestamp, p) for p in after if p.timestamp), default=(None, None)
            )
            date_prefix = start.strftime("%Y.%m.%d") if start else "YYYY.MM.DD"
            new_cluster = Cluster(
                id=new_id,
                name=f"{date_prefix} \u2013 Untitled",
                photos=after,
                centroid_lat=clat,
                centroid_lon=clon,
                locked=False,
                action="accept",
            )
            self._clusters.append(new_cluster)
            self._build_table()
            self.notify(f"Split into {len(before)} + {len(after)} photos.")

        self._save_undo()
        self.push_screen(SplitDialog(), _apply)

    def action_go(self) -> None:
        self._resolve_merges()
        self.exit(self._clusters)

    def action_quit_app(self) -> None:
        self.exit([])  # empty list signals abort

    # ------------------------------------------------------------------
    # Merge resolution: move photos from merge-sources into their targets
    # ------------------------------------------------------------------

    def _resolve_merges(self) -> None:
        to_remove: set[int] = set()
        for cluster in self._clusters:
            if cluster.action == "merge" and cluster.merge_target_id is not None:
                target = self._cluster_by_id(cluster.merge_target_id)
                if target is not None:
                    target.photos.extend(cluster.photos)
                to_remove.add(cluster.id)
        self._clusters = [c for c in self._clusters if c.id not in to_remove]
