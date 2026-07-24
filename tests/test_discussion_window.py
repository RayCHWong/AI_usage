# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>
#
# Part of "usage". Free software licensed under the GNU Affero General Public
# License v3.0 only; see the LICENSE file for full terms and the warranty disclaimer.

from __future__ import annotations

import json
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
    def __init__(self) -> None:
        self.started: tuple[str, list[object], str | None] | None = None
        self.stop_count = 0

    def start(
        self,
        topic: str,
        participants: list[object],
        moderator_id: str | None,
    ) -> str:
        self.started = (topic, participants, moderator_id)
        return "session"

    def stop(self) -> None:
        self.stop_count += 1

    def snapshot(self) -> dict[str, object]:
        return {"session_id": "session", "status": "PREPARING"}

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
            }
        )
    )

    assert action.topic == "問題"
    assert action.participants == ("claude", "codex")
    assert action.moderator_id == "codex"


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
    assert bridge.stop_count == 1
    assert all(script.startswith("window.discussionApplySnapshot(") for script in webview.scripts)


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


def test_html_uses_isolated_handler_and_safe_dynamic_dom() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'const HANDLER = "usageDiscussion"' in html
    assert "window.discussionApplyEvents" in html
    assert "window.discussionApplySnapshot" in html
    assert "window.discussionApplyDetection" in html
    assert "window.discussionApplyError" in html
    assert ".innerHTML" not in html
    assert "createElement" in html
    assert "textContent" in html
    assert "prefers-color-scheme" in html
    assert "event.session_id !== currentSessionId" in html
    assert "sequence <= latestEventSeq" in html


def test_html_visible_static_elements_use_i18n_keys() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")

    for key in (
        "discussion_topic_label",
        "discussion_topic_placeholder",
        "discussion_participants",
        "discussion_moderator",
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
