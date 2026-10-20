"""Create and verify the harness views of the canonical skill tree.

A view is a single directory symlink (POSIX) or junction (Windows) to
``.github/skills``.  Skill bodies are never copied.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .model import CANONICAL_REL
from .paths import create_dir_link, is_link_like, points_to, remove_dir_link

STATE_REL = Path(".github/skills/_shared/.skillkit/views.json")


@dataclass(frozen=True)
class View:
    key: str
    relative: Path
    label: str


VIEWS: tuple[View, ...] = (
    View("agents", Path(".agents/skills"), "DSH, Cursor, Gemini CLI, Copilot (VS 2026)"),
    View("claude", Path(".claude/skills"), "Claude Code"),
    View("codex", Path(".codex/skills"), "Codex"),
)

ALIASES = {
    "all": None,
    "agents": "agents",
    "dsh": "agents",
    "cursor": "agents",
    "gemini": "agents",
    "copilot": "agents",
    "claude": "claude",
    "claude-code": "claude",
    "codex": "codex",
}


class SyncError(RuntimeError):
    """Raised when a view cannot be created or verified safely."""


def select_views(harness: str) -> list[View]:
    key = harness.strip().lower()
    if key not in ALIASES:
        raise SyncError(f"unknown harness '{harness}'; choose from: {', '.join(sorted(ALIASES))}")
    selected = ALIASES[key]
    if selected is None:
        return list(VIEWS)
    return [view for view in VIEWS if view.key == selected]


def sync_views(
    repo_root: Path,
    *,
    harness: str = "agents",
    check: bool = False,
    force: bool = False,
) -> list[str]:
    canonical = (repo_root / CANONICAL_REL).resolve()
    if not canonical.is_dir():
        raise SyncError(f"canonical skill root not found: {canonical}")

    state = _load_state(repo_root)
    messages: list[str] = []
    for view in select_views(harness):
        path = repo_root / view.relative
        if check:
            messages.append(_check_view(path, canonical, view))
            continue
        messages.append(_ensure_view(path, canonical, view, state, force))

    if not check:
        _save_state(repo_root, state)
    return messages


def _ensure_view(
    path: Path,
    canonical: Path,
    view: View,
    state: dict,
    force: bool,
) -> str:
    recorded = state["views"].get(view.relative.as_posix())

    if path.exists() or is_link_like(path):
        if is_link_like(path):
            if points_to(path, canonical):
                state["views"][view.relative.as_posix()] = {
                    "target": canonical.as_posix(),
                    "method": recorded.get("method", _link_method(path)) if recorded else _link_method(path),
                }
                return f"ok: {view.relative.as_posix()} -> {canonical} ({view.label})"
            if not force and not recorded:
                raise SyncError(
                    f"{view.relative.as_posix()} is a link to {path.resolve()}, not {canonical}; "
                    "pass --force to replace it"
                )
            remove_dir_link(path)
        elif path.is_dir():
            entries = list(path.iterdir())
            if entries and not force:
                raise SyncError(
                    f"{view.relative.as_posix()} is a real directory with {len(entries)} entr(y/ies); "
                    "move it away or pass --force (skillkit never deletes unrecorded content)"
                )
            if entries:
                _remove_tree(path)
            else:
                path.rmdir()
        else:
            if not force:
                raise SyncError(f"{view.relative.as_posix()} exists and is not a directory")
            path.unlink()

    method = create_dir_link(path, canonical)
    if not points_to(path, canonical):
        raise SyncError(f"created {view.relative.as_posix()} but it does not resolve to {canonical}")
    state["views"][view.relative.as_posix()] = {
        "target": canonical.as_posix(),
        "method": method,
    }
    return f"created: {view.relative.as_posix()} -> {canonical} ({method}, {view.label})"


def _check_view(path: Path, canonical: Path, view: View) -> str:
    if not path.exists():
        raise SyncError(
            f"{view.relative.as_posix()} is missing; run "
            "`python .github/skills/_shared/skillkit/cli.py sync`"
        )
    if not is_link_like(path):
        raise SyncError(
            f"{view.relative.as_posix()} is a real directory, not a link; "
            "skillkit refuses to treat it as a view"
        )
    if not points_to(path, canonical):
        raise SyncError(
            f"{view.relative.as_posix()} points to {path.resolve()}, not {canonical}"
        )
    return f"ok: {view.relative.as_posix()} -> {canonical} ({view.label})"


def remove_views(repo_root: Path, *, harness: str = "agents") -> list[str]:
    state = _load_state(repo_root)
    canonical = (repo_root / CANONICAL_REL).resolve()
    messages: list[str] = []
    for view in select_views(harness):
        path = repo_root / view.relative
        if not path.exists() and not is_link_like(path):
            messages.append(f"absent: {view.relative.as_posix()}")
            continue
        if is_link_like(path) and points_to(path, canonical):
            remove_dir_link(path)
            messages.append(f"removed: {view.relative.as_posix()}")
        elif state["views"].get(view.relative.as_posix()):
            if is_link_like(path):
                remove_dir_link(path)
            else:
                _remove_tree(path)
            messages.append(f"removed: {view.relative.as_posix()}")
        else:
            raise SyncError(
                f"{view.relative.as_posix()} is not a recorded skillkit view; refusing to delete"
            )
        state["views"].pop(view.relative.as_posix(), None)
        _prune_empty_parent(path)
    _save_state(repo_root, state)
    return messages


def _prune_empty_parent(view_path: Path) -> None:
    """Remove an empty .agents/.claude/.codex parent left after removing a view."""

    parent = view_path.parent
    if parent.name not in {".agents", ".claude", ".codex"}:
        return
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def status(repo_root: Path) -> str:
    canonical = (repo_root / CANONICAL_REL).resolve()
    lines = [
        f"canonical: {canonical}",
        "views (default: .agents; optional: .claude = Claude Code, .codex = Codex):",
    ]
    for view in VIEWS:
        path = repo_root / view.relative
        if not path.exists():
            lines.append(f"  {view.relative.as_posix():18} absent")
        elif is_link_like(path) and points_to(path, canonical):
            lines.append(f"  {view.relative.as_posix():18} ok -> {canonical}")
        elif is_link_like(path):
            lines.append(f"  {view.relative.as_posix():18} link -> {path.resolve()} (WRONG)")
        else:
            lines.append(f"  {view.relative.as_posix():18} real directory (NOT A VIEW)")
    return "\n".join(lines)


def _load_state(repo_root: Path) -> dict:
    path = repo_root / STATE_REL
    if not path.is_file():
        return {"version": 1, "views": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "views": {}}
    if not isinstance(data, dict) or not isinstance(data.get("views"), dict):
        return {"version": 1, "views": {}}
    return data


def _save_state(repo_root: Path, state: dict) -> None:
    path = repo_root / STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _remove_tree(path: Path) -> None:
    for child in path.iterdir():
        if is_link_like(child):
            remove_dir_link(child)
        elif child.is_dir():
            _remove_tree(child)
        else:
            child.unlink()
    path.rmdir()


def _link_method(path: Path) -> str:
    if os.name == "nt" and not os.path.islink(path):
        return "junction"
    return "symlink"
