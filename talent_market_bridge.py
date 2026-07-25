# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>
#
# Part of "usage". Free software licensed under the GNU Affero General Public
# License v3.0 only; see the LICENSE file for full terms and the warranty
# disclaimer.

# mypy: disable-error-code="import-untyped,import-not-found,misc"
"""Bridge to the external ``instate-cli`` executable that powers the
"AI 人才市場" panel.

The CLI is built in a separate project (``~/Developer/instate``) and bundled
into ``vendor/instate-cli`` at .app build time. It prints one JSON object per
invocation on stdout. Every wrapper here returns a dict and never raises, so a
missing/broken CLI degrades to an empty-state panel instead of crashing the
popover. Folder selection uses a native ``NSOpenPanel`` (no CLI round-trip).
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _bundled_cli_path() -> Path | None:
    try:
        from Foundation import NSBundle
    except ImportError:
        return None
    bundle = NSBundle.mainBundle()
    if bundle is None:
        return None
    # py2app flattens a single-file resource to Resources/<basename>, so the
    # compiled CLI lives at Resources/instate-cli. In dev (python3 main.py) the
    # main bundle is the Python framework and this returns None → fall back to
    # repo vendor/instate-cli.
    path = bundle.pathForResource_ofType_("instate-cli", "")
    if path:
        return Path(str(path))
    return None


def _cli_path() -> Path:
    bundled = _bundled_cli_path()
    if bundled is not None:
        return bundled
    vendor = "vendor"
    name = "instate-cli"
    return _repo_root() / vendor / name


def _run(args: list[str]) -> dict[str, Any]:
    cli = _cli_path()
    if not cli.exists():
        return {"ok": False, "status": "missing", "error": "instate-cli not installed"}
    try:
        proc = subprocess.run(  # noqa: S603 — fixed local binary, args are constants/IDs
            [str(cli), *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timeout", "error": "instate-cli timed out"}
    except Exception as exc:
        # Any failure to spawn/read (OSError, decode hiccups, ...) degrades to
        # an error dict — the panel shows its empty state instead of crashing.
        return {"ok": False, "status": "error", "error": str(exc)}
    if proc.returncode != 0:
        return {
            "ok": False,
            "status": "error",
            "error": (proc.stderr or "").strip() or f"instate-cli exited {proc.returncode}",
        }
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "status": "error",
            "error": "instate-cli returned non-JSON",
            "stdout": proc.stdout,
        }
    if not isinstance(parsed, dict):
        return {"ok": False, "status": "error", "error": "instate-cli returned non-object"}
    return parsed


def list_state(lang: str | None = None) -> dict[str, Any]:
    args = ["list-state"]
    if lang:
        args.append(lang)
    return _run(args)


def list_personas(lang: str | None = None) -> list[dict[str, str]]:
    """Return every available role as a flat, discussion-friendly record."""
    try:
        state = list_state(lang)
    except Exception:
        return []
    packs = state.get("packs")
    if not isinstance(packs, list):
        return []
    personas: list[dict[str, str]] = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        pack_id = pack.get("id")
        pack_name = pack.get("name")
        if not isinstance(pack_id, str) or not isinstance(pack_name, str):
            pack_id = ""
            pack_name = ""
        roles = pack.get("roles")
        if not isinstance(roles, list):
            continue
        for role in roles:
            if not isinstance(role, dict):
                continue
            role_id = role.get("id")
            name = role.get("name")
            persona_name = role.get("personaName")
            description = role.get("description")
            system_prompt = role.get("systemPrompt")
            if (
                not isinstance(role_id, str)
                or not isinstance(name, str)
                or not isinstance(persona_name, str)
                or not isinstance(description, str)
                or not isinstance(system_prompt, str)
            ):
                continue
            personas.append(
                {
                    "id": role_id,
                    "name": name,
                    "persona_name": persona_name,
                    "description": description,
                    "system_prompt": system_prompt,
                    "pack_id": pack_id,
                    "pack_name": pack_name,
                }
            )
    return personas


def install_role(role_id: str) -> dict[str, Any]:
    return _run(["install", role_id])


def launch_role(role_id: str, task_prompt: str | None = None) -> dict[str, Any]:
    args = ["launch", role_id]
    if task_prompt:
        args.append(task_prompt)
    return _run(args)


def restore_role(role_id: str) -> dict[str, Any]:
    return _run(["restore", role_id])


def ignore_drift(role_id: str) -> dict[str, Any]:
    return _run(["ignore-drift", role_id])


def set_folder(role_id: str, path: str) -> dict[str, Any]:
    return _run(["set-folder", role_id, path])


def pick_folder() -> str | None:
    """Open a native folder picker; return the chosen path or ``None``.

    The popover's content window sits at ``NSNormalWindowLevel`` and usage runs
    as an accessory app (no Dock icon). A bare ``runModal`` opens the panel at
    the same level, so it lands behind the still-open popover and never takes
    focus — the user clicks "選擇資料夾" and nothing visible happens. Two nudges
    fix it: activate the app so the panel can become key, and raise the panel
    one notch above ``NSPopUpMenuWindowLevel`` so it clears the popover.
    """
    try:
        from AppKit import NSApp, NSOpenPanel, NSPopUpMenuWindowLevel
    except ImportError:
        return None
    NSApp.activateIgnoringOtherApps_(True)
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(False)
    panel.setCanChooseDirectories_(True)
    panel.setAllowsMultipleSelection_(False)
    panel.setResolvesAliases_(True)
    panel.setLevel_(NSPopUpMenuWindowLevel + 1)
    if panel.runModal() != 1:  # NSModalResponseOK
        return None
    urls = panel.URLs()
    if urls is None or len(urls) == 0:
        return None
    url = urls[0]
    if url is None:
        return None
    path = url.path()
    return str(path) if path else None


def pick_image_file() -> str | None:
    """Open a native image picker; return the chosen path or ``None``.

    Shares the activation/level fixups of :func:`pick_folder` so the panel lands
    in front of the discussion window instead of behind it.
    """
    try:
        from AppKit import NSApp, NSOpenPanel, NSPopUpMenuWindowLevel
    except ImportError:
        return None
    NSApp.activateIgnoringOtherApps_(True)
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(False)
    panel.setAllowsMultipleSelection_(False)
    panel.setResolvesAliases_(True)
    panel.setAllowedFileTypes_(["png", "jpg", "jpeg", "gif", "webp"])
    panel.setLevel_(NSPopUpMenuWindowLevel + 1)
    if panel.runModal() != 1:  # NSModalResponseOK
        return None
    urls = panel.URLs()
    if urls is None or len(urls) == 0:
        return None
    url = urls[0]
    if url is None:
        return None
    path = url.path()
    return str(path) if path else None
