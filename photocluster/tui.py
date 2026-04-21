from __future__ import annotations

import subprocess
from datetime import datetime
from typing import Literal, Optional

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

try:
    from PIL import Image as PilImage, ImageOps
    from textual_image.widget import Image as ImageWidget
    _HAS_IMAGE = True
except ImportError:
    _HAS_IMAGE = False

_PREVIEW_WIDTH_PX = 900

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
        Binding("left", "focus_left", "", show=False, priority=True),
        Binding("right", "focus_right", "", show=False, priority=True),
        Binding("r", "rename", "Rename"),
        Binding("k", "toggle_skip", "Skip/Accept"),
        Binding("m", "merge", "Merge"),
        Binding("s", "split", "Split"),
        Binding("e", "open_earliest", "Earliest"),
        Binding("b", "open_middle", "Bisect"),
        Binding("l", "open_latest", "Latest"),
        Binding("n", "open_next_day", "Next day"),
        Binding("o", "open_file", "Open"),
        Binding("f", "explore_folder", "Folder"),
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
    #main-layout {
        height: 1fr;
    }
    #cluster-table {
        width: 2fr;
    }
    #files-table {
        width: 1fr;
        border-left: solid $primary;
    }
    #preview-pane {
        width: 1fr;
        min-width: 36;
        border-left: solid $primary;
        padding: 0 1;
    }
    #preview-label {
        height: 1;
        color: $text-muted;
        margin: 1 0;
        text-overflow: ellipsis;
    }
    #preview-image {
        width: auto;
        height: auto;
    }
    #preview-placeholder {
        height: 1fr;
        content-align: center middle;
        color: $text-disabled;
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

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            f"  Mode: [bold]{self._mode}[/bold]   "
            f"Destination: [bold]{self._output}[/bold]   "
            f"  [dim]r[/dim]:Rename  [dim]k[/dim]:Skip  [dim]m[/dim]:Merge  [dim]s[/dim]:Split  "
            f"[dim]e[/dim]:Earliest  [dim]l[/dim]:Latest  [dim]f[/dim]:Folder  "
            f"[dim]g/Enter[/dim]:Apply  [dim]q[/dim]:Quit",
            id="info-bar",
            markup=True,
        )
        with Horizontal(id="main-layout"):
            yield DataTable(id="cluster-table", cursor_type="row")
            yield DataTable(id="files-table", cursor_type="row")
            with Vertical(id="preview-pane"):
                yield Label("", id="preview-label")
                if _HAS_IMAGE:
                    yield ImageWidget(id="preview-image")
                else:
                    yield Static(
                        "pip install textual-image\nfor inline previews",
                        id="preview-placeholder",
                    )
        yield Footer()

    def on_mount(self) -> None:
        files_table = self.query_one("#files-table", DataTable)
        files_table.add_column("#", key="idx", width=5)
        files_table.add_column("File", key="name")
        files_table.add_column("Time", key="time", width=14)
        files_table.add_column("GPS", key="gps", width=10)
        self._build_table()
        cluster = self._current_cluster()
        if cluster is not None:
            self._populate_files_table(cluster)

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

    @on(DataTable.RowHighlighted, "#cluster-table")
    def _on_cluster_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        cluster = self._cluster_by_id(int(str(event.row_key.value)))
        if cluster is not None:
            self._populate_files_table(cluster)

    @on(DataTable.RowHighlighted, "#files-table")
    def _on_file_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        path_str = str(event.row_key.value)
        cluster = self._current_cluster()
        if cluster is None:
            return
        photo = next((p for p in cluster.photos if str(p.path) == path_str), None)
        if photo is not None:
            self._update_preview(photo)

    def _populate_files_table(self, cluster: Cluster) -> None:
        table = self.query_one("#files-table", DataTable)
        table.clear()
        photos = self._sorted_photos_by_time(cluster)
        for i, photo in enumerate(photos):
            ts = photo.timestamp.strftime("%Y-%m-%d %H:%M") if photo.timestamp else "–"
            gps = Text("no GPS", style="red") if not photo.has_gps else Text("ok", style="dim green")
            table.add_row(str(i + 1), photo.path.name, ts, gps, key=str(photo.path))
        if photos:
            self._update_preview(photos[0])

    @work(exclusive=True, thread=True)
    def _update_preview(self, photo: Photo) -> None:
        from textual.worker import get_current_worker
        worker = get_current_worker()
        self.call_from_thread(
            self.query_one("#preview-label", Label).update, photo.path.name
        )
        if not _HAS_IMAGE:
            return
        try:
            img = PilImage.open(photo.path)
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            orig_w, orig_h = img.size
            target_h = int(orig_h * _PREVIEW_WIDTH_PX / orig_w)
            img = img.resize((_PREVIEW_WIDTH_PX, target_h), PilImage.LANCZOS)
            if not worker.is_cancelled:
                self.call_from_thread(self._set_preview_image, img)
        except Exception:
            pass

    def _set_preview_image(self, img: object) -> None:
        if _HAS_IMAGE:
            self.query_one("#preview-image", ImageWidget).image = img  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_focus_left(self) -> None:
        files_table = self.query_one("#files-table", DataTable)
        if self.focused is files_table:
            self.query_one("#cluster-table", DataTable).focus()

    def action_focus_right(self) -> None:
        cluster_table = self.query_one("#cluster-table", DataTable)
        if self.focused is cluster_table:
            self.query_one("#files-table", DataTable).focus()

    def action_rename(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return

        def _apply(new_name: Optional[str]) -> None:
            if new_name:
                cluster.name = new_name
                self._refresh_row(cluster)

        self.push_screen(RenameDialog(cluster.name), _apply)

    def action_toggle_skip(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return
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

        self.push_screen(SplitDialog(), _apply)

    def _sorted_photos_by_time(self, cluster: Cluster) -> list[Photo]:
        with_ts = [p for p in cluster.photos if p.timestamp is not None]
        without_ts = [p for p in cluster.photos if p.timestamp is None]
        return sorted(with_ts, key=lambda p: p.timestamp) + without_ts  # type: ignore[arg-type]

    def _navigate_files_to(self, index: int) -> None:
        table = self.query_one("#files-table", DataTable)
        table.focus()
        table.move_cursor(row=index)

    def action_open_earliest(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return
        photos = self._sorted_photos_by_time(cluster)
        if not photos:
            self.notify("No photos in cluster.", severity="warning")
            return
        self._navigate_files_to(0)

    def action_open_latest(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return
        photos = self._sorted_photos_by_time(cluster)
        if not photos:
            self.notify("No photos in cluster.", severity="warning")
            return
        self._navigate_files_to(len(photos) - 1)

    def action_open_file(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return
        files_table = self.query_one("#files-table", DataTable)
        cur = files_table.cursor_row
        photos = self._sorted_photos_by_time(cluster)
        if not (0 <= cur < len(photos)):
            return
        subprocess.Popen(["open", str(photos[cur].path)])

    def action_open_middle(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return
        photos = self._sorted_photos_by_time(cluster)
        if not photos:
            self.notify("No photos in cluster.", severity="warning")
            return
        self._navigate_files_to(len(photos) // 2)

    def action_open_next_day(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return
        photos = self._sorted_photos_by_time(cluster)
        if not photos:
            self.notify("No photos in cluster.", severity="warning")
            return
        files_table = self.query_one("#files-table", DataTable)
        cur = files_table.cursor_row
        current_date = photos[cur].timestamp.date() if 0 <= cur < len(photos) and photos[cur].timestamp else None
        target = len(photos) - 1
        if current_date is not None:
            for i, p in enumerate(photos):
                if p.timestamp and p.timestamp.date() > current_date:
                    target = i
                    break
        self._navigate_files_to(target)

    def action_explore_folder(self) -> None:
        cluster = self._current_cluster()
        if cluster is None:
            return
        if not cluster.photos:
            self.notify("No photos in cluster.", severity="warning")
            return
        folder = cluster.photos[0].path.parent
        subprocess.Popen(["open", str(folder)])

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
