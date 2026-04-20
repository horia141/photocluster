from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from photocluster.executor import (
    UNDO_LOG_NAME,
    _safe_dirname,
    _same_filesystem,
    _unique_path,
    apply_plan,
    undo_last_run,
)
from photocluster.models import Cluster, Photo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _photo(path: Path) -> Photo:
    return Photo(path=path, mtime=0.0, timestamp=None, lat=None, lon=None)


def _cluster(cid: int, name: str, photos: list[Photo], action: str = "accept") -> Cluster:
    c = Cluster(id=cid, name=name, photos=photos, action=action)  # type: ignore[arg-type]
    return c


# ---------------------------------------------------------------------------
# _safe_dirname
# ---------------------------------------------------------------------------


def test_safe_dirname_clean():
    assert _safe_dirname("2024.07.14 \u2013 Dubrovnik") == "2024.07.14 \u2013 Dubrovnik"


def test_safe_dirname_invalid_chars():
    assert _safe_dirname('Trip: "Summer" <2024>') == "Trip_ _Summer_ _2024_"


def test_safe_dirname_strips():
    assert _safe_dirname("  name  ") == "name"


# ---------------------------------------------------------------------------
# _unique_path
# ---------------------------------------------------------------------------


def test_unique_path_no_collision(tmp_path):
    p = tmp_path / "photo.jpg"
    assert _unique_path(p) == p


def test_unique_path_one_collision(tmp_path):
    p = tmp_path / "photo.jpg"
    p.touch()
    result = _unique_path(p)
    assert result == tmp_path / "photo_1.jpg"


def test_unique_path_multiple_collisions(tmp_path):
    p = tmp_path / "photo.jpg"
    p.touch()
    (tmp_path / "photo_1.jpg").touch()
    result = _unique_path(p)
    assert result == tmp_path / "photo_2.jpg"


# ---------------------------------------------------------------------------
# apply_plan — copy mode
# ---------------------------------------------------------------------------


def test_apply_plan_copy(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    out = tmp_path / "output"

    img = src / "test.jpg"
    img.write_bytes(b"fake jpeg")

    cluster = _cluster(0, "2024.07.14 \u2013 Test", [_photo(img)])
    result = apply_plan([cluster], source=src, output=out, mode="cp")

    assert (out / "2024.07.14 \u2013 Test" / "test.jpg").exists()
    assert img.exists()  # original untouched
    assert result is None  # no undo log for cp


# ---------------------------------------------------------------------------
# apply_plan — move mode + undo
# ---------------------------------------------------------------------------


def test_apply_plan_move(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    out = tmp_path / "output"

    img = src / "test.jpg"
    img.write_bytes(b"fake jpeg")

    cluster = _cluster(0, "Trip", [_photo(img)])
    undo_log = apply_plan([cluster], source=src, output=out, mode="mv")

    assert (out / "Trip" / "test.jpg").exists()
    assert not img.exists()
    assert undo_log is not None and undo_log.exists()


def test_undo_restores_files(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    out = tmp_path / "output"

    img = src / "test.jpg"
    img.write_bytes(b"fake jpeg")

    cluster = _cluster(0, "Trip", [_photo(img)])
    apply_plan([cluster], source=src, output=out, mode="mv")
    assert not img.exists()

    undo_last_run(out)
    assert img.exists()
    assert not (out / UNDO_LOG_NAME).exists()


def test_undo_no_log(tmp_path, capsys):
    undo_last_run(tmp_path)
    captured = capsys.readouterr()
    # Rich output goes to stderr; just ensure no exception
    # (Rich Console writes to stdout by default in tests)


# ---------------------------------------------------------------------------
# apply_plan — skip action
# ---------------------------------------------------------------------------


def test_apply_plan_skip_leaves_files(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    out = tmp_path / "output"

    img = src / "test.jpg"
    img.write_bytes(b"fake jpeg")

    cluster = _cluster(0, "Trip", [_photo(img)], action="skip")
    apply_plan([cluster], source=src, output=out, mode="cp")

    assert img.exists()
    assert not (out / "Trip").exists()


# ---------------------------------------------------------------------------
# apply_plan — symlink mode
# ---------------------------------------------------------------------------


def test_apply_plan_symlink_relative(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    out = tmp_path / "output"

    img = src / "test.jpg"
    img.write_bytes(b"fake jpeg")

    cluster = _cluster(0, "Trip", [_photo(img)])
    apply_plan([cluster], source=src, output=out, mode="ln", symlink_type="rel")

    link = out / "Trip" / "test.jpg"
    assert link.is_symlink()
    # Relative symlink should resolve correctly
    assert link.resolve() == img.resolve()


# ---------------------------------------------------------------------------
# dry_run
# ---------------------------------------------------------------------------


def test_dry_run_touches_nothing(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    out = tmp_path / "output"

    img = src / "test.jpg"
    img.write_bytes(b"fake jpeg")

    cluster = _cluster(0, "Trip", [_photo(img)])
    result = apply_plan([cluster], source=src, output=out, mode="mv", dry_run=True)

    assert img.exists()
    assert not out.exists()
    assert result is None


# ---------------------------------------------------------------------------
# filename collision
# ---------------------------------------------------------------------------


def test_apply_plan_filename_collision(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    out = tmp_path / "output"

    img1 = src / "test.jpg"
    img2 = src / "subdir" / "test.jpg"
    img2.parent.mkdir()
    img1.write_bytes(b"image one")
    img2.write_bytes(b"image two")

    cluster = _cluster(0, "Trip", [_photo(img1), _photo(img2)])
    apply_plan([cluster], source=src, output=out, mode="cp")

    dest_dir = out / "Trip"
    assert (dest_dir / "test.jpg").exists()
    assert (dest_dir / "test_1.jpg").exists()
