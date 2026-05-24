"""把 usage 的 statusLine hook 安裝到 / 從 Claude Code 設定移除。

Claude Code 透過 ~/.claude/settings.json 的 statusLine 欄位，
在每次刷新狀態列時呼叫指定指令並餵 JSON 給 stdin。
我們把專案內的 usage_statusline.py 複製到 ~/.claude/usage-statusline.py，
然後把 statusLine 指向它，讓它把 JSON 落地到磁碟給 usage 主程式讀。

備份原 statusLine 到 settings["usage"]["previousStatusLine"]，
unsetup 時還原。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import stat
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

CLAUDE_SETTINGS = Path(os.path.expanduser("~/.claude/settings.json"))
HOOK_TARGET = Path(os.path.expanduser("~/.claude/usage-statusline.py"))
FORWARDER_TARGET = Path(os.path.expanduser("~/.claude/usage-statusline-forwarder.py"))
STATUS_FILE = Path(os.path.expanduser("~/.claude/usage-status.json"))
CODEX_CONFIG = Path(os.path.expanduser("~/.codex/config.toml"))
CODEX_BACKUP = Path(os.path.expanduser("~/.codex/usage-backup.json"))
# LEGACY_TT_* / tokenTracker / tt-* below are MIGRATION-ONLY constants for users
# upgrading from the third-party tool stormzhang/token-tracker. They are NOT links
# to any in-repo module or external directory. Do not investigate or "go look" for
# a token-tracker source — none exists. See SESSION.md "token-tracker 已徹底不存在".
LEGACY_CODEX_BACKUP = Path(os.path.expanduser("~/.codex/tt-backup.json"))
CODEX_STATUS_LINE = [
    "project",
    "five-hour-limit",
    "weekly-limit",
    "context-remaining",
    "model-with-reasoning",
]
LEGACY_NAME = "usag"
LEGACY_HOOK_TARGET = Path(os.path.expanduser(f"~/.claude/{LEGACY_NAME}-statusline.py"))
LEGACY_STATUS_FILE = Path(os.path.expanduser(f"~/.claude/{LEGACY_NAME}-status.json"))
LEGACY_TT_HOOK_TARGET = Path(os.path.expanduser("~/.claude/tt-statusline.py"))
BACKUP_KEY = "usage"
LEGACY_TT_BACKUP_KEY = "tokenTracker"
LEGACY_BACKUP_KEY = LEGACY_NAME
PREV_SL_KEY = "previousStatusLine"
HOOK_VERSION = "1.0"
_SL_REGEX = re.compile(r"status_line\s*=\s*\[.*?\]", re.DOTALL)


def _resolve_hook_source() -> Path:
    paths = [
        Path(__file__).resolve().parent / "usage_statusline.py",
        Path(sys.executable).resolve().parent.parent / "Resources" / "usage_statusline.py",
    ]
    for path in paths:
        if path.exists():
            return path
    tried = ", ".join(str(path) for path in paths)
    raise SystemExit(f"❌ 找不到 hook 原始檔，tried: {tried}")


def _resolve_forwarder_source() -> Path:
    paths = [
        Path(__file__).resolve().parent / "usage_statusline_forwarder.py",
        (
            Path(sys.executable).resolve().parent.parent
            / "Resources"
            / "usage_statusline_forwarder.py"
        ),
    ]
    for path in paths:
        if path.exists():
            return path
    tried = ", ".join(str(path) for path in paths)
    raise SystemExit(f"❌ 找不到 forwarder 原始檔，tried: {tried}")


def _statusline_command() -> str:
    # 用系統 python3，不綁 venv（hook 只用標準庫）
    python = shutil.which("python3") or "python3"
    return f"{_shell_arg(python)} {_shell_arg(str(HOOK_TARGET))}"


def _shell_arg(value: str) -> str:
    return shlex.quote(value)


def _forwarder_command() -> str:
    python = shutil.which("python3") or "python3"
    return f"{shlex.quote(python)} {shlex.quote(str(FORWARDER_TARGET))}"


def _is_usage_hook(sl: object) -> bool:
    if not isinstance(sl, dict):
        return False
    cmd = sl.get("command")
    return isinstance(cmd, str) and "usage-statusline" in cmd


def _is_legacy_tt_hook(sl: object) -> bool:
    if not isinstance(sl, dict):
        return False
    cmd = sl.get("command")
    return isinstance(cmd, str) and "tt-statusline" in cmd


def _detect_current_state(settings: dict[str, Any] | None = None) -> str:
    """返回 'none' | 'us-direct' | 'us-forwarder' | 'external'."""
    data = _load_settings() if settings is None else settings
    sl = data.get("statusLine")
    if not isinstance(sl, dict):
        return "none"
    cmd = sl.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return "none"
    if "usage-statusline-forwarder" in cmd:
        return "us-forwarder"
    if "usage-statusline" in cmd:
        return "us-direct"
    if "tt-statusline" in cmd:
        return "legacy-tt"
    return "external"


def _migrate_from_legacy_usage() -> None:
    changed = False

    for path in (LEGACY_HOOK_TARGET, LEGACY_STATUS_FILE):
        try:
            if path.exists():
                path.unlink()
                changed = True
        except OSError as exc:
            print(f"⚠ 無法移除舊檔 {path}: {exc}")

    settings: dict[str, Any] | None = None
    try:
        if CLAUDE_SETTINGS.exists():
            with CLAUDE_SETTINGS.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                settings = data
            else:
                print(f"⚠ {CLAUDE_SETTINGS} 不是 JSON object，略過 migration")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"⚠ 無法讀取舊設定做 migration: {exc}")

    if settings is not None:
        try:
            sl = settings.get("statusLine")
            cmd = sl.get("command") if isinstance(sl, dict) else None
            if (
                isinstance(cmd, str)
                and f"{LEGACY_NAME}-statusline" in cmd
                and "usage-statusline" not in cmd
            ):
                settings.pop("statusLine", None)
                changed = True
        except Exception as exc:
            print(f"⚠ 無法清理舊 statusLine: {exc}")

        try:
            legacy_backup = settings.pop(LEGACY_BACKUP_KEY, None)
            legacy_tt_backup = settings.pop(LEGACY_TT_BACKUP_KEY, None)
            current_backup = settings.get(BACKUP_KEY)
            merged: dict[str, Any] = {}
            if isinstance(legacy_backup, dict):
                merged.update(legacy_backup)
            if isinstance(legacy_tt_backup, dict):
                merged.update(legacy_tt_backup)
            if isinstance(merged, dict) and merged:
                if isinstance(current_backup, dict):
                    settings[BACKUP_KEY] = {**merged, **current_backup}
                else:
                    settings[BACKUP_KEY] = merged
                changed = True
            elif legacy_backup is not None or legacy_tt_backup is not None:
                changed = True
        except Exception as exc:
            print(f"⚠ 無法搬移舊備份 key: {exc}")

        if changed:
            try:
                _save_settings(settings)
            except Exception as exc:
                print(f"⚠ 無法寫回 migration 設定: {exc}")

    if changed:
        print(f"ℹ 已從 v0.1.x ({LEGACY_NAME}) 自動 migrate 到 usage")


def _load_settings() -> dict[str, Any]:
    if not CLAUDE_SETTINGS.exists():
        return {}
    try:
        with CLAUDE_SETTINGS.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"❌ 無法讀取 {CLAUDE_SETTINGS}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"❌ {CLAUDE_SETTINGS} 必須是 JSON object")
    return data


def _save_settings(data: dict[str, Any]) -> None:
    CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=CLAUDE_SETTINGS.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, CLAUDE_SETTINGS)
        tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def _copy_hook_script() -> None:
    hook_source = _resolve_hook_source()
    HOOK_TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(hook_source, HOOK_TARGET)
    HOOK_TARGET.chmod(HOOK_TARGET.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_forwarder_script() -> None:
    forwarder_source = _resolve_forwarder_source()
    FORWARDER_TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(forwarder_source, FORWARDER_TARGET)
    FORWARDER_TARGET.chmod(
        FORWARDER_TARGET.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )


def _backup_existing_statusline(settings: dict[str, Any]) -> None:
    existing = settings.get("statusLine")
    if not existing or _is_usage_hook(existing):
        return
    backup = settings.get(BACKUP_KEY)
    if not isinstance(backup, dict):
        backup = {}
        settings[BACKUP_KEY] = backup
    backup[PREV_SL_KEY] = existing
    print(f"ℹ 已備份原有 statusLine 到 settings.{BACKUP_KEY}.{PREV_SL_KEY}")


def _status_line_toml(items: list[str]) -> str:
    body = ",\n".join(f'  "{item}"' for item in items)
    return f"status_line = [\n{body},\n]"


def _read_codex_config() -> tuple[str, dict[str, Any]] | None:
    try:
        content = CODEX_CONFIG.read_text(encoding="utf-8")
        parsed = tomllib.loads(content)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return content, parsed


def _codex_status_line(parsed: dict[str, Any]) -> object:
    tui = parsed.get("tui")
    return tui.get("status_line") if isinstance(tui, dict) else None


def _setup_codex() -> None:
    result = _read_codex_config()
    if not result:
        return
    content, parsed = result

    old = _codex_status_line(parsed)
    if old == CODEX_STATUS_LINE:
        print("ℹ Codex status_line 已是 usage 設定，略過")
        return

    if old is not None:
        CODEX_BACKUP.parent.mkdir(parents=True, exist_ok=True)
        CODEX_BACKUP.write_text(
            json.dumps({"status_line": old}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        content = _SL_REGEX.sub(_status_line_toml(CODEX_STATUS_LINE), content)
    elif "[tui]" in content:
        content = content.replace("[tui]", f"[tui]\n{_status_line_toml(CODEX_STATUS_LINE)}")
    else:
        content += f"\n[tui]\n{_status_line_toml(CODEX_STATUS_LINE)}\n"

    CODEX_CONFIG.write_text(content, encoding="utf-8")
    print("✓ Codex status_line 已配置")
    if old is not None:
        print(f"ℹ 原配置已備份到: {CODEX_BACKUP}")
    print("ℹ 請重新開啟 Codex 一次")


def _unsetup_codex() -> None:
    result = _read_codex_config()
    if not result:
        return
    content, parsed = result

    if _codex_status_line(parsed) is None:
        return

    backup_path = CODEX_BACKUP if CODEX_BACKUP.exists() else LEGACY_CODEX_BACKUP
    if backup_path.exists():
        try:
            old_items = json.loads(backup_path.read_text(encoding="utf-8")).get("status_line", [])
        except (OSError, json.JSONDecodeError, AttributeError):
            old_items = []
        content = _SL_REGEX.sub(_status_line_toml(old_items), content)
        backup_path.unlink(missing_ok=True)
        print("✓ Codex status_line 已恢復原配置")
    else:
        content = re.sub(r"status_line\s*=\s*\[.*?\]\n?", "", content, flags=re.DOTALL)
        print("✓ Codex status_line 已移除")

    CODEX_CONFIG.write_text(content, encoding="utf-8")


def _installed_hook_version() -> str | None:
    try:
        with HOOK_TARGET.open(encoding="utf-8") as f:
            for line in f:
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return None


def needs_update() -> bool:
    if not HOOK_TARGET.parent.exists():
        return False
    return _installed_hook_version() != HOOK_VERSION


def update_hook() -> None:
    if not HOOK_TARGET.parent.exists():
        return
    _copy_hook_script()


def is_setup() -> bool:
    has_claude = CLAUDE_SETTINGS.parent.exists()
    has_codex = CODEX_CONFIG.exists()
    if not has_claude and not has_codex:
        return False

    if has_claude and _detect_current_state() not in {"us-direct", "us-forwarder"}:
        return False

    if has_codex:
        result = _read_codex_config()
        if not result:
            return False
        _, parsed = result
        if _codex_status_line(parsed) != CODEX_STATUS_LINE:
            return False

    return True


def _install_forwarder(settings: dict[str, Any]) -> None:
    """複製 usage_statusline_forwarder.py 到 ~/.claude/，更新 settings.json."""
    _copy_hook_script()
    _copy_forwarder_script()
    _backup_existing_statusline(settings)
    settings["statusLine"] = {"type": "command", "command": _forwarder_command()}
    _save_settings(settings)


def setup(force_forwarder: bool = False) -> int:
    _migrate_from_legacy_usage()
    has_claude = CLAUDE_SETTINGS.parent.exists()
    has_codex = CODEX_CONFIG.exists()
    if not has_claude and not has_codex:
        print("❌ 找不到 Claude Code 或 Codex，請先安裝並執行其中之一", file=sys.stderr)
        return 1

    if has_claude:
        settings = _load_settings()
        state = _detect_current_state(settings)

        if force_forwarder or state in {"external", "legacy-tt"}:
            _install_forwarder(settings)
            print(f"✓ forwarder 已安裝：{FORWARDER_TARGET}")
            print(f"✓ hook 已安裝：{HOOK_TARGET}")
            print(f"✓ settings 已更新：{CLAUDE_SETTINGS}")
            print("ℹ 請重新開啟 Claude Code 一次（讓它重新讀 settings 並刷新一次 statusLine）")
        else:
            _copy_hook_script()
            if state == "none":
                settings["statusLine"] = {"type": "command", "command": _statusline_command()}
                _save_settings(settings)
            elif state in {"us-direct", "us-forwarder"}:
                print("ℹ statusLine 已是 usage hook，settings 未動")

            print(f"✓ hook 已安裝：{HOOK_TARGET}")
            print(f"✓ settings 已更新：{CLAUDE_SETTINGS}")
            print("ℹ 請重新開啟 Claude Code 一次（讓它重新讀 settings 並刷新一次 statusLine）")

    if has_codex:
        _setup_codex()

    return 0


def unsetup() -> int:
    if CLAUDE_SETTINGS.parent.exists():
        settings = _load_settings()
        sl = settings.get("statusLine")

        if _is_usage_hook(sl) or _is_legacy_tt_hook(sl):
            backup = settings.get(BACKUP_KEY)
            legacy_backup = settings.get(LEGACY_TT_BACKUP_KEY)
            prev = backup.get(PREV_SL_KEY) if isinstance(backup, dict) else None
            if not isinstance(prev, dict) and isinstance(legacy_backup, dict):
                prev = legacy_backup.get(PREV_SL_KEY)

            if isinstance(prev, dict):
                settings["statusLine"] = prev
                print("✓ 已還原原有 statusLine")
            else:
                settings.pop("statusLine", None)
                print("✓ 已移除 usage statusLine")

            if isinstance(backup, dict):
                backup.pop(PREV_SL_KEY, None)
                if not backup:
                    del settings[BACKUP_KEY]
            if isinstance(legacy_backup, dict):
                legacy_backup.pop(PREV_SL_KEY, None)
                if not legacy_backup:
                    del settings[LEGACY_TT_BACKUP_KEY]

            _save_settings(settings)
        else:
            print("ℹ statusLine 不是 usage 安裝的，settings 未動")

        for path in (HOOK_TARGET, FORWARDER_TARGET, LEGACY_TT_HOOK_TARGET):
            if path.exists():
                path.unlink()
                print(f"✓ 已刪除 hook：{path}")

        if STATUS_FILE.exists():
            STATUS_FILE.unlink()
            print(f"✓ 已刪除狀態檔：{STATUS_FILE}")

    if CODEX_CONFIG.exists():
        _unsetup_codex()

    return 0
