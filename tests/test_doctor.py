from __future__ import annotations

import json
from pathlib import Path

import pytest

import codex_loader
import doctor
import setup_hook


def test_doctor_handles_missing_settings_and_status_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_hook, "CLAUDE_SETTINGS", tmp_path / ".claude" / "settings.json")
    monkeypatch.setattr(setup_hook, "HOOK_TARGET", tmp_path / ".claude" / "usage-statusline.py")
    monkeypatch.setattr(
        setup_hook,
        "FORWARDER_TARGET",
        tmp_path / ".claude" / "usage-statusline-forwarder.py",
    )
    monkeypatch.setattr(setup_hook, "STATUS_FILE", tmp_path / ".claude" / "usage-status.json")
    monkeypatch.setattr(codex_loader, "SESSIONS_DIR", tmp_path / ".codex" / "sessions")

    output = doctor.render()

    assert "usage v" in output
    assert "hook state:        none" in output
    assert "status file:" in output
    assert "self-heal log (last 5):\n  none" in output


def test_doctor_reports_external_hook_keyword(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    settings = claude_dir / "settings.json"
    settings.write_text(
        json.dumps({"statusLine": {"type": "command", "command": "node /opt/ccusage/bin/cli"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_hook, "CLAUDE_SETTINGS", settings)
    monkeypatch.setattr(setup_hook, "STATUS_FILE", claude_dir / "usage-status.json")
    monkeypatch.setattr(codex_loader, "SESSIONS_DIR", tmp_path / ".codex" / "sessions")

    output = doctor.render()

    assert "hook state:        external" in output
    assert "external hooks:    ccusage" in output
