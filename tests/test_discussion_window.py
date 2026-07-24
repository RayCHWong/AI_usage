# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>
#
# Part of "usage". Free software licensed under the GNU Affero General Public
# License v3.0 only; see the LICENSE file for full terms and the warranty disclaimer.

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast

import pytest

import discussion_window

HTML_PATH = Path(__file__).resolve().parents[1] / "assets" / "windows" / "discussion.html"


class FakeWebView:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def evaluateJavaScript_completionHandler_(
        self,
        script: str,
        completion: object,
    ) -> None:
        self.scripts.append(script)


class FakeBridge:
    def __init__(self, working_directory: str | None = None) -> None:
        self.started: tuple[str, list[object], str | None, str | None] | None = None
        self.stop_count = 0
        self.working_directory = working_directory

    def start(
        self,
        topic: str,
        participants: list[object],
        moderator_id: str | None,
        working_directory: str | None = None,
    ) -> str:
        self.started = (topic, participants, moderator_id, working_directory)
        self.working_directory = working_directory
        return "session"

    def stop(self) -> None:
        self.stop_count += 1

    def snapshot(self) -> dict[str, object]:
        return {
            "session_id": "session",
            "status": "PREPARING",
            "working_directory": self.working_directory,
        }

    def detect_participants(self) -> list[object]:
        return []

    def set_event_listener(self, callback: object) -> None:
        return None


class VisibleMarkupTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in {"script", "style"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.hidden_depth == 0 and data.strip():
            self.text.append(data.strip())


@pytest.mark.parametrize(
    ("raw", "action"),
    [
        ('{"action":"discussion_attach"}', "discussion_attach"),
        ('{"action":"discussion_detect"}', "discussion_detect"),
        ('{"action":"discussion_pick_folder"}', "discussion_pick_folder"),
        ('{"action":"discussion_clear_folder"}', "discussion_clear_folder"),
        ('{"action":"discussion_stop"}', "discussion_stop"),
    ],
)
def test_parse_simple_actions(raw: str, action: str) -> None:
    assert discussion_window.parse_discussion_action(raw).action == action


def test_parse_start_action_validates_and_normalizes_fields() -> None:
    action = discussion_window.parse_discussion_action(
        json.dumps(
            {
                "action": "discussion_start",
                "topic": "問題",
                "participants": ["claude", "codex"],
                "moderatorId": "codex",
                "workingDir": "/tmp/project",
            }
        )
    )

    assert action.topic == "問題"
    assert action.participants == ("claude", "codex")
    assert action.moderator_id == "codex"
    assert action.working_directory == "/tmp/project"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"action": "unknown"},
        {"action": "discussion_start", "topic": 1, "participants": ["claude"]},
        {"action": "discussion_start", "topic": "x", "participants": []},
        {"action": "discussion_start", "topic": "x", "participants": ["other"]},
        {
            "action": "discussion_start",
            "topic": "x",
            "participants": ["claude", "claude"],
        },
        {
            "action": "discussion_start",
            "topic": "x",
            "participants": ["claude"],
            "moderatorId": "codex",
        },
        {
            "action": "discussion_start",
            "topic": "x",
            "participants": ["claude"],
            "workingDir": 1,
        },
    ],
)
def test_parse_action_rejects_bad_parameters(payload: object) -> None:
    with pytest.raises(ValueError):
        discussion_window.parse_discussion_action(json.dumps(payload))


@pytest.mark.parametrize(
    ("participant_count", "expected"),
    [(-1, 0), (0, 0), (1, 1), (2, 5), (3, 7), (5, 11)],
)
def test_estimate_cli_calls(participant_count: int, expected: int) -> None:
    assert discussion_window.estimate_cli_calls(participant_count) == expected


def test_javascript_serialization_keeps_untrusted_text_as_json_data() -> None:
    payload = {"text": '"; alert(1); //\n</script>'}

    script = discussion_window.serialize_javascript_call("discussionApplyError", payload)
    encoded = script.removeprefix("window.discussionApplyError(").removesuffix(")")

    assert json.loads(encoded) == payload
    assert script.startswith("window.discussionApplyError(")


def test_event_batch_adds_snapshot_streaming_metadata_without_mutation() -> None:
    events = [
        {
            "session_id": "session",
            "event_seq": 1,
            "kind": "turn_started",
            "participant_id": "codex",
            "turn_id": "turn",
            "payload": {"round_index": 1},
        }
    ]
    snapshot = {
        "turns": [
            {
                "id": "turn",
                "supports_token_stream": False,
            }
        ]
    }

    script = discussion_window.serialize_event_batch(events, snapshot)
    encoded = script.removeprefix("window.discussionApplyEvents(").removesuffix(")")
    result = json.loads(encoded)

    assert result[0]["payload"]["supports_token_stream"] is False
    assert events[0]["payload"] == {"round_index": 1}


@pytest.mark.skipif(sys.platform != "darwin", reason="PyObjC action shell is macOS-only")
def test_controller_dispatches_start_and_stop_actions() -> None:
    bridge = FakeBridge()
    controller = discussion_window.DiscussionWindowController(bridge=cast(Any, bridge))
    webview = FakeWebView()
    controller._attached = True
    controller._web_ready = True
    controller.webview = webview

    controller._receive_action(
        json.dumps(
            {
                "action": "discussion_start",
                "topic": "問題",
                "participants": ["claude", "codex"],
                "moderatorId": "codex",
                "workingDir": "/tmp/project",
            }
        )
    )
    controller._receive_action('{"action":"discussion_stop"}')

    assert bridge.started is not None
    assert bridge.started[0] == "問題"
    assert [cast(Any, participant).id for participant in bridge.started[1]] == [
        "claude",
        "codex",
    ]
    assert bridge.started[2] == "codex"
    assert bridge.started[3] == "/tmp/project"
    assert bridge.stop_count == 1
    assert any(script.startswith("window.discussionApplySnapshot(") for script in webview.scripts)


@pytest.mark.skipif(sys.platform != "darwin", reason="PyObjC action shell is macOS-only")
def test_controller_converts_bad_action_to_javascript_error() -> None:
    controller = discussion_window.DiscussionWindowController(
        bridge=cast(Any, FakeBridge()),
    )
    webview = FakeWebView()
    controller._attached = True
    controller._web_ready = True
    controller.webview = webview

    controller._receive_action('{"action":"discussion_start","topic":3}')

    assert len(webview.scripts) == 1
    assert webview.scripts[0].startswith("window.discussionApplyError(")
    assert "requires a string topic" in webview.scripts[0]


@pytest.mark.skipif(sys.platform != "darwin", reason="PyObjC action shell is macOS-only")
def test_controller_picks_and_clears_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = str(tmp_path / "project")
    monkeypatch.setattr(discussion_window, "pick_folder", lambda: selected)
    controller = discussion_window.DiscussionWindowController(
        bridge=cast(Any, FakeBridge()),
    )
    webview = FakeWebView()
    controller._attached = True
    controller._web_ready = True
    controller.webview = webview

    controller._receive_action('{"action":"discussion_pick_folder"}')
    script_count = len(webview.scripts)
    monkeypatch.setattr(discussion_window, "pick_folder", lambda: None)
    controller._receive_action('{"action":"discussion_pick_folder"}')
    assert len(webview.scripts) == script_count
    controller._receive_action('{"action":"discussion_clear_folder"}')

    assert webview.scripts[-2:] == [
        discussion_window.serialize_javascript_call(
            "discussionApplyWorkingDir", selected
        ),
        discussion_window.serialize_javascript_call(
            "discussionApplyWorkingDir", None
        ),
    ]


@pytest.mark.skipif(sys.platform != "darwin", reason="PyObjC action shell is macOS-only")
def test_controller_restores_working_directory_on_attach(tmp_path: Path) -> None:
    selected = str(tmp_path / "project")
    controller = discussion_window.DiscussionWindowController(
        bridge=cast(Any, FakeBridge(selected)),
    )
    webview = FakeWebView()
    controller._attached = True
    controller._web_ready = True
    controller.webview = webview

    controller._apply_full_state()

    assert discussion_window.serialize_javascript_call(
        "discussionApplyWorkingDir", selected
    ) in webview.scripts


def test_html_uses_isolated_handler_and_safe_dynamic_dom() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'const HANDLER = "usageDiscussion"' in html
    assert "window.discussionApplyEvents" in html
    assert "window.discussionApplySnapshot" in html
    assert "window.discussionApplyDetection" in html
    assert "window.discussionApplyWorkingDir" in html
    assert "window.discussionApplyError" in html
    assert ".innerHTML" not in html
    assert "createElement" in html
    assert "textContent" in html
    assert "prefers-color-scheme" in html
    assert "event.session_id !== currentSessionId" in html
    assert "sequence <= latestEventSeq" in html
    assert "workingDirectoryPathEl.textContent" in html
    assert "workingDir: workingDirectory" in html


def test_failed_turn_error_is_collapsed_with_first_line_summary() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'document.createElement("details")' in html
    assert 'document.createElement("summary")' in html
    assert "fullError.split(/\\r?\\n/, 1)[0]" in html
    assert "summaryText.textContent = firstLine" in html
    assert "summaryText.title = firstLine" in html
    assert "error.textContent = fullError" in html
    assert "details.open" not in html
    assert ".turn-error-summary-text" in html
    assert "text-overflow: ellipsis" in html


def test_participant_chips_use_project_icons_and_inline_agy_badge() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "max-width: min(250px, 100%)" in html
    assert "grid-template-columns: auto auto minmax(0, 1fr)" in html
    assert "flex-wrap: wrap" in html
    assert "const PARTICIPANT_ICON_URIS" in html
    assert '"{{CLAUDE_ICON}}"' in html
    assert '"{{CODEX_ICON}}"' in html
    assert "const AGY_BADGE" in html
    assert "const DEFAULT_PARTICIPANT_BADGE" in html
    assert 'document.createElement("img")' in html
    assert 'badge.className = "participant-badge"' in html
    assert 'badge.alt = ""' in html
    assert 'document.createElementNS("http://www.w3.org/2000/svg", "svg")' in html
    assert 'badge.setAttribute("aria-hidden", "true")' in html
    assert "chip.append(checkbox, createParticipantBadge(id), name, status)" in html
    assert "url(http" not in html


def test_discussion_html_injects_existing_project_icon_data_uris(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        discussion_window,
        "_data_uri",
        lambda name: f"data:image/webp;base64,{name}",
    )

    html = discussion_window._load_discussion_html("en")

    assert "{{CLAUDE_ICON}}" not in html
    assert "{{CODEX_ICON}}" not in html
    assert "data:image/webp;base64,claude.webp" in html
    assert "data:image/webp;base64,codex.webp" in html


@pytest.mark.parametrize(
    ("topic", "participant_count", "status", "expected"),
    [
        ("", 1, "IDLE", False),
        (" \n\t", 1, "COMPLETED", False),
        ("question", 0, "IDLE", False),
        ("question", 1, "IDLE", True),
        ("question", 2, "COMPLETED", True),
        ("question", 1, "PREPARING", False),
        ("question", 1, "ROUND1_RUNNING", False),
        ("question", 1, "ROUND2_RUNNING", False),
        ("question", 1, "SUMMARIZING", False),
        ("question", 1, "CANCELLING", False),
        ("question", 1, "FAILED", True),
    ],
)
def test_start_button_logic(
    topic: str,
    participant_count: int,
    status: str,
    expected: bool,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to evaluate the pure browser control function")
    html = HTML_PATH.read_text(encoding="utf-8")
    statuses = re.search(
        r"    const RUNNING_STATUSES = new Set\(\[.*?^    \]\);",
        html,
        flags=re.MULTILINE | re.DOTALL,
    )
    function = re.search(
        r"    function canStartDiscussion\(.*?^    \}",
        html,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert statuses is not None
    assert function is not None
    invocation = (
        f"{statuses.group(0)}\n{function.group(0)}\n"
        "process.stdout.write(JSON.stringify(canStartDiscussion("
        f"{json.dumps(topic)}, {participant_count}, {json.dumps(status)})));"
    )

    result = subprocess.run(
        [node, "-e", invocation],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) is expected


def test_html_controls_and_history_follow_use_reviewed_logic() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "function canStartDiscussion(topic, participantCount, status)" in html
    assert "startEl.disabled = !canStartDiscussion(" in html
    assert "stopEl.disabled = !running" in html
    assert "PARTICIPANT_IDS.filter((id) => selected.has(id))" in html
    assert "function isHistoryNearBottom()" in html
    assert "return distance < 80" in html
    assert "if (wasNearBottom && shouldFollow)" in html
    assert "scrollHistoryToBottom()" in html
    assert "historyEl.scrollTop = previousScrollTop" in html


def test_html_colors_are_tokenized_with_light_mode_overrides() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    styles = html.split("<style>", 1)[1].split("</style>", 1)[0]

    assert "@media (prefers-color-scheme: light)" in styles
    assert "@media (prefers-color-scheme: dark)" not in styles
    assert "color: white" not in styles
    assert "background: transparent" not in styles


def test_copy_feedback_uses_i18n_and_four_section_plain_text() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "function summaryForClipboard()" in html
    assert "SUMMARY_HEADINGS.map" in html
    assert 't("discussion_copied")' in html
    assert 't("discussion_copy_failed")' in html
    assert "JSON.stringify(summaryText)" not in html


def test_html_visible_static_elements_use_i18n_keys() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")

    for key in (
        "discussion_topic_label",
        "discussion_topic_placeholder",
        "discussion_participants",
        "discussion_moderator",
        "discussion_working_directory",
        "discussion_pick_folder",
        "discussion_clear_folder",
        "discussion_working_directory_none",
        "discussion_working_directory_warning",
        "discussion_start",
        "discussion_stop",
        "discussion_history",
        "discussion_summary",
        "discussion_copy",
    ):
        assert f'"{key}"' in html
    parser = VisibleMarkupTextParser()
    parser.feed(html)
    assert parser.text == []


def test_window_source_keeps_bridge_logic_out_and_main_thread_drain_batched() -> None:
    source = Path(discussion_window.__file__).read_text(encoding="utf-8")

    assert "class _DiscussionWindow(NSWindow)" in source
    assert "def canBecomeMainWindow" in source
    assert "def canBecomeKeyWindow" in source
    assert "drain_events(50)" in source
    assert "evaluateJavaScript_completionHandler_" in source
    assert "run_streaming" not in source
    assert "subprocess" not in source
    assert "build_round1_prompt" not in source
