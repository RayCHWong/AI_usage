from __future__ import annotations

import os
import shlex
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import setup_hook
from i18n import packaged_resource_path

SEPARATOR = "-" * 29


def render() -> str:
    lines = [
        f"usage v{_field(_current_version)}",
        SEPARATOR,
        f"hook state:        {_field(_hook_state)}",
        f"hook version:      {_field(_hook_version)}",
        f"hook script:       {_script_status(setup_hook.HOOK_TARGET)}",
        f"forwarder script:  {_script_status(setup_hook.FORWARDER_TARGET)}",
        f"status file:       {_field(_status_file)}",
        f"external hooks:    {_field(_external_hooks)}",
        f"forwarder prompt:  {_field(_forwarder_prompt)}",
        "self-heal log (last 5):",
        *_self_heal_log_lines(),
        SEPARATOR,
        f"codex sessions:    {_field(_codex_sessions)}",
    ]
    return "\n".join(lines) + "\n"


def _field(func: Callable[[], str]) -> str:
    try:
        return func()
    except Exception as exc:
        return f"error: {exc}"


def _current_version() -> str:
    try:
        return metadata.version("usage")
    except metadata.PackageNotFoundError:
        pyproject = packaged_resource_path(
            "pyproject.toml", Path(__file__).with_name("pyproject.toml")
        )
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        version = data.get("project", {}).get("version")
        if isinstance(version, str):
            return version
        raise RuntimeError("project.version missing from pyproject.toml") from None


def _hook_state() -> str:
    return setup_hook._detect_current_state()


def _hook_version() -> str:
    installed = setup_hook._installed_hook_version()
    if installed is None:
        return f"not installed (current {setup_hook.HOOK_VERSION})"
    suffix = (
        "current"
        if installed == setup_hook.HOOK_VERSION
        else f"current {setup_hook.HOOK_VERSION}"
    )
    return f"{installed} ({suffix})"


def _script_status(path: Path) -> str:
    try:
        display = _display_path(path)
        status = "ok" if path.exists() else "missing"
        return f"{display}  [{status}]"
    except Exception as exc:
        return f"error: {exc}"


def _status_file() -> str:
    path = setup_hook.STATUS_FILE
    display = _display_path(path)
    if not path.exists():
        return f"{display}  [missing]"
    return f"{display}  (wrote {_ago(path.stat().st_mtime)} ago)"


def _external_hooks() -> str:
    state = setup_hook._detect_current_state()
    if state != "external":
        return "none detected"
    settings = setup_hook._load_settings()
    sl = settings.get("statusLine")
    command = sl.get("command") if isinstance(sl, dict) else None
    if not isinstance(command, str):
        return "external (unrecognized)"
    keyword = _external_keyword(command)
    return keyword if keyword else "external (unrecognized)"


def _forwarder_prompt() -> str:
    settings = setup_hook._load_settings()
    usage = settings.get(setup_hook.BACKUP_KEY)
    if isinstance(usage, dict) and usage.get("forwarderModePromptDismissed") is True:
        return "acked"
    return "not acked"


def _self_heal_log_lines() -> list[str]:
    try:
        settings = setup_hook._load_settings()
        usage = settings.get(setup_hook.BACKUP_KEY)
        log = usage.get("selfHealLog") if isinstance(usage, dict) else None
        if not isinstance(log, list) or not log:
            return ["  none"]
        lines: list[str] = []
        for item in log[-5:]:
            if not isinstance(item, dict):
                continue
            timestamp = str(item.get("timestamp", "unknown"))
            action = str(item.get("action", "unknown"))
            detail = str(item.get("detail", ""))
            lines.append(f"  {timestamp}  {action:<22} {detail}".rstrip())
        return lines or ["  none"]
    except Exception as exc:
        return [f"  error: {exc}"]


def _codex_sessions() -> str:
    import codex_loader

    sessions_dir = codex_loader.SESSIONS_DIR
    if not sessions_dir.is_dir():
        return "0 files scanned, ok"
    count = 0
    for _ in sessions_dir.rglob("*.jsonl"):
        count += 1
    return f"{count} files scanned, ok"


def _external_keyword(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    for part in parts:
        token = part.lower()
        basename = Path(part).name.lower()
        for keyword in ("ccusage", "lord-kali"):
            if keyword in token or keyword in basename:
                return keyword
    return None


def _display_path(path: Path) -> str:
    home = str(Path.home())
    text = str(path)
    if text == home:
        return "~"
    if text.startswith(home + os.sep):
        return "~" + text[len(home) :]
    return text


def _ago(mtime: float) -> str:
    seconds = max(0, int(datetime.now(UTC).timestamp() - mtime))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"
