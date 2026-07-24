# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>
#
# Part of "usage". Free software licensed under the GNU Affero General Public
# License v3.0 only; see the LICENSE file for full terms and the warranty disclaimer.

"""macOS window shell for the PyObjC-free AI council bridge."""

# mypy: disable-error-code="import-untyped,import-not-found,misc"
from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

from discussion_bridge import DiscussionBridge, ParticipantSpec
from discussion_cli import DetectionResult
from i18n import _load_i18n_bundle, _t, packaged_resource_path
from usage_lang import detect_lang

SCRIPT_HANDLER_NAME = "usageDiscussion"
WINDOW_AUTOSAVE_NAME = "usage.discussion.window"
BUILTIN_PARTICIPANTS = ("claude", "codex", "gemini")
PARTICIPANT_LABELS = {
    "claude": "Claude",
    "codex": "Codex",
    "gemini": "Gemini",
}
RUNNING_STATUSES = frozenset(
    {"PREPARING", "ROUND1_RUNNING", "ROUND2_RUNNING", "SUMMARIZING", "CANCELLING"}
)

if sys.platform == "darwin":
    import objc
    from AppKit import (
        NSApp,
        NSApplicationActivateAllWindows,
        NSApplicationActivateIgnoringOtherApps,
        NSBackingStoreBuffered,
        NSMakeRect,
        NSViewHeightSizable,
        NSViewWidthSizable,
        NSWindow,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskResizable,
        NSWindowStyleMaskTitled,
    )
    from Foundation import NSURL, NSObject, NSRunningApplication, NSThread

    try:
        from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration
    except ModuleNotFoundError:
        with objc.autorelease_pool():
            objc.loadBundle(
                "WebKit",
                globals(),
                bundle_path="/System/Library/Frameworks/WebKit.framework",
            )

    objc.registerMetaDataForSelector(
        b"WKWebView",
        b"evaluateJavaScript:completionHandler:",
        {
            "arguments": {
                3: {
                    "callable": {
                        "retval": {"type": b"v"},
                        "arguments": {
                            0: {"type": b"^v"},
                            1: {"type": b"@"},
                            2: {"type": b"@"},
                        },
                    },
                },
            },
        },
    )


ActionName = Literal[
    "discussion_attach",
    "discussion_detect",
    "discussion_start",
    "discussion_stop",
]


@dataclass(frozen=True)
class DiscussionAction:
    action: ActionName
    topic: str | None = None
    participants: tuple[str, ...] = ()
    moderator_id: str | None = None


def parse_discussion_action(raw: object) -> DiscussionAction:
    """Validate one JSON-string action without touching PyObjC or the bridge."""
    if not isinstance(raw, str):
        raise ValueError("action message must be a JSON string")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("action message is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("action message must contain an object")
    action = payload.get("action")
    if action not in {
        "discussion_attach",
        "discussion_detect",
        "discussion_start",
        "discussion_stop",
    }:
        raise ValueError("unknown discussion action")
    if action != "discussion_start":
        return DiscussionAction(cast(ActionName, action))

    topic = payload.get("topic")
    participant_value = payload.get("participants")
    moderator_value = payload.get("moderatorId")
    if not isinstance(topic, str):
        raise ValueError("discussion_start requires a string topic")
    if not isinstance(participant_value, list) or not participant_value:
        raise ValueError("discussion_start requires at least one participant")
    if not all(isinstance(item, str) for item in participant_value):
        raise ValueError("discussion_start participants must be strings")
    participants = tuple(cast(list[str], participant_value))
    if len(participants) != len(set(participants)):
        raise ValueError("discussion_start participants must be unique")
    if any(item not in BUILTIN_PARTICIPANTS for item in participants):
        raise ValueError("discussion_start contains an unknown participant")
    if moderator_value is not None and not isinstance(moderator_value, str):
        raise ValueError("discussion_start moderatorId must be a string or null")
    moderator_id = moderator_value
    if moderator_id is not None and moderator_id not in participants:
        raise ValueError("discussion_start moderatorId must be selected")
    return DiscussionAction(
        cast(ActionName, action),
        topic=topic,
        participants=participants,
        moderator_id=moderator_id,
    )


def estimate_cli_calls(participant_count: int) -> int:
    """Return the maximum calls shown before a discussion starts."""
    if participant_count <= 0:
        return 0
    if participant_count == 1:
        return 1
    return participant_count * 2 + 1


def serialize_javascript_call(function_name: str, payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"window.{function_name}({encoded})"


def serialize_event_batch(
    events: Sequence[Mapping[str, object]],
    snapshot: Mapping[str, object],
) -> str:
    """Add snapshot-only turn metadata before sending one JavaScript batch."""
    turn_streaming: dict[str, bool] = {}
    turns = snapshot.get("turns")
    if isinstance(turns, list):
        for item in turns:
            if not isinstance(item, dict):
                continue
            turn_id = item.get("id")
            supports_stream = item.get("supports_token_stream")
            if isinstance(turn_id, str) and isinstance(supports_stream, bool):
                turn_streaming[turn_id] = supports_stream

    enriched: list[dict[str, object]] = []
    for source_event in events:
        event = dict(source_event)
        turn_id = event.get("turn_id")
        payload_value = event.get("payload")
        payload = dict(payload_value) if isinstance(payload_value, dict) else {}
        if isinstance(turn_id, str) and turn_id in turn_streaming:
            payload["supports_token_stream"] = turn_streaming[turn_id]
        event["payload"] = payload
        enriched.append(event)
    return serialize_javascript_call("discussionApplyEvents", enriched)


def _load_discussion_html(language: str | None = None) -> str:
    path = packaged_resource_path(
        "windows/discussion.html",
        Path(__file__).with_name("assets") / "windows" / "discussion.html",
    )
    html = path.read_text(encoding="utf-8")
    return (
        html.replace(
            "{{I18N_BUNDLE}}",
            json.dumps(_load_i18n_bundle(), ensure_ascii=False),
        )
        .replace(
            "{{INITIAL_LANGUAGE}}",
            json.dumps(language or detect_lang()),
        )
    )


if sys.platform == "darwin":

    class _DiscussionWindow(NSWindow):
        def canBecomeMainWindow(self) -> bool:
            return True

        def canBecomeKeyWindow(self) -> bool:
            return True


    class _DiscussionScriptHandler(NSObject):
        controller = objc.ivar()

        def initWithController_(self, controller: Any) -> Any:
            self = objc.super(_DiscussionScriptHandler, self).init()
            if self is None:
                return None
            self.controller = controller
            return self

        def userContentController_didReceiveScriptMessage_(
            self,
            user_content_controller: Any,
            message: Any,
        ) -> None:
            self.controller._receive_action(message.body())


    class _DiscussionWindowDelegate(NSObject):
        controller = objc.ivar()

        def initWithController_(self, controller: Any) -> Any:
            self = objc.super(_DiscussionWindowDelegate, self).init()
            if self is None:
                return None
            self.controller = controller
            return self

        def windowWillClose_(self, notification: Any) -> None:
            self.controller._detach()

        def webView_didFinishNavigation_(self, webview: Any, navigation: Any) -> None:
            self.controller._webview_did_finish()


    class _MainThreadDispatcher(NSObject):
        controller = objc.ivar()

        def initWithController_(self, controller: Any) -> Any:
            self = objc.super(_MainThreadDispatcher, self).init()
            if self is None:
                return None
            self.controller = controller
            return self

        def drainDiscussionEvents_(self, sender: Any) -> None:
            self.controller._drain_events_on_main_thread()


class DiscussionWindowController:
    """Own the standalone NSWindow and forward bridge state to its web view."""

    def __init__(self, bridge: DiscussionBridge | None = None) -> None:
        self.bridge = bridge or DiscussionBridge()
        self.window: Any | None = None
        self.webview: Any | None = None
        self._content_controller: Any | None = None
        self._script_handler: Any | None = None
        self._window_delegate: Any | None = None
        self._dispatcher: Any | None = None
        self._attached = False
        self._web_ready = False
        self._shutdown = False
        self._drain_scheduled = False
        self._drain_lock = threading.Lock()
        self._language = detect_lang()
        if sys.platform == "darwin":
            self._dispatcher = _MainThreadDispatcher.alloc().initWithController_(self)

    def show(self, close_popover: Callable[[], None] | None = None) -> None:
        self._require_main_thread()
        if self._shutdown:
            raise RuntimeError("discussion window controller is shut down")
        if close_popover is not None:
            close_popover()
        if self.window is None:
            self._create_window()
        window = self.window
        assert window is not None
        self._attach()
        NSApp.activateIgnoringOtherApps_(True)
        NSRunningApplication.currentApplication().activateWithOptions_(
            NSApplicationActivateIgnoringOtherApps | NSApplicationActivateAllWindows
        )
        window.makeMainWindow()
        window.makeKeyWindow()
        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()
        if self._web_ready:
            self._apply_full_state()
        self._schedule_drain_on_main_thread()

    def close(self) -> None:
        self._require_main_thread()
        if self.window is not None:
            self.window.performClose_(None)

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        self._require_main_thread()
        if self._shutdown:
            return
        self._shutdown = True
        self._detach()
        self.bridge.shutdown(timeout_seconds)
        if self._content_controller is not None:
            self._content_controller.removeScriptMessageHandlerForName_(SCRIPT_HANDLER_NAME)
        if self.webview is not None:
            self.webview.setNavigationDelegate_(None)
            self.webview.stopLoading()
        if self.window is not None:
            self.window.setDelegate_(None)
            self.window.orderOut_(None)
        self.webview = None
        self.window = None
        self._content_controller = None
        self._script_handler = None
        self._window_delegate = None

    def _create_window(self) -> None:
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self.window = _DiscussionWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 900, 640),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_(_t(self._language, "discussion_window_title"))
        self.window.setReleasedWhenClosed_(False)
        self.window.setFrameAutosaveName_(WINDOW_AUTOSAVE_NAME)
        self._window_delegate = _DiscussionWindowDelegate.alloc().initWithController_(self)
        self.window.setDelegate_(self._window_delegate)
        self.window.center()

        configuration = WKWebViewConfiguration.alloc().init()
        self._content_controller = WKUserContentController.alloc().init()
        self._script_handler = _DiscussionScriptHandler.alloc().initWithController_(self)
        self._content_controller.addScriptMessageHandler_name_(
            self._script_handler,
            SCRIPT_HANDLER_NAME,
        )
        configuration.setUserContentController_(self._content_controller)
        self.webview = WKWebView.alloc().initWithFrame_configuration_(
            self.window.contentView().bounds(),
            configuration,
        )
        self.webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.webview.setNavigationDelegate_(self._window_delegate)
        self.window.setContentView_(self.webview)
        html_path = packaged_resource_path(
            "windows/discussion.html",
            Path(__file__).with_name("assets") / "windows" / "discussion.html",
        )
        base_url = NSURL.fileURLWithPath_(str(html_path.parent))
        self.webview.loadHTMLString_baseURL_(_load_discussion_html(self._language), base_url)

    def _attach(self) -> None:
        self._attached = True
        self.bridge.set_event_listener(self._bridge_events_ready)

    def _detach(self) -> None:
        self._attached = False
        self.bridge.set_event_listener(None)
        with self._drain_lock:
            self._drain_scheduled = False

    def _bridge_events_ready(self) -> None:
        if not self._attached or self._shutdown or self._dispatcher is None:
            return
        self._dispatcher.performSelectorOnMainThread_withObject_waitUntilDone_(
            "drainDiscussionEvents:",
            None,
            False,
        )

    def _schedule_drain_on_main_thread(self) -> None:
        self._require_main_thread()
        if not self._attached or not self._web_ready or self.webview is None:
            return
        with self._drain_lock:
            if self._drain_scheduled:
                return
            self._drain_scheduled = True
        dispatcher = self._dispatcher
        assert dispatcher is not None
        dispatcher.performSelector_withObject_afterDelay_(
            "drainDiscussionEvents:",
            None,
            0.0,
        )

    def _drain_events_on_main_thread(self) -> None:
        self._require_main_thread()
        with self._drain_lock:
            self._drain_scheduled = False
        if not self._attached or not self._web_ready or self.webview is None:
            return
        events = self.bridge.drain_events(50)
        if events:
            script = serialize_event_batch(events, self.bridge.snapshot())
            self.webview.evaluateJavaScript_completionHandler_(script, None)
        if len(events) == 50:
            self._schedule_drain_on_main_thread()

    def _webview_did_finish(self) -> None:
        self._require_main_thread()
        if self._shutdown or self.webview is None:
            return
        self._web_ready = True
        if self._attached:
            self._apply_full_state()
            self._schedule_drain_on_main_thread()

    def _receive_action(self, raw: object) -> None:
        self._require_main_thread()
        try:
            action = parse_discussion_action(raw)
            if action.action == "discussion_attach":
                self._apply_full_state()
            elif action.action == "discussion_detect":
                self._apply_detection()
            elif action.action == "discussion_stop":
                self.bridge.stop()
                self._apply_snapshot()
            else:
                assert action.topic is not None
                specs = [
                    ParticipantSpec(
                        id=participant_id,
                        label=PARTICIPANT_LABELS[participant_id],
                        adapter_id=participant_id,
                    )
                    for participant_id in action.participants
                ]
                self.bridge.start(action.topic, specs, action.moderator_id)
                self._apply_snapshot()
        except Exception as exc:
            self._evaluate("discussionApplyError", str(exc))

    def _apply_full_state(self) -> None:
        if not self._attached or not self._web_ready:
            return
        self._apply_snapshot()
        self._apply_detection()

    def _apply_snapshot(self) -> None:
        self._evaluate("discussionApplySnapshot", self.bridge.snapshot())

    def _apply_detection(self) -> None:
        detections: list[DetectionResult] = self.bridge.detect_participants()
        self._evaluate(
            "discussionApplyDetection",
            [asdict(detection) for detection in detections],
        )

    def _evaluate(self, function_name: str, payload: object) -> None:
        self._require_main_thread()
        if not self._attached or not self._web_ready or self.webview is None:
            return
        self.webview.evaluateJavaScript_completionHandler_(
            serialize_javascript_call(function_name, payload),
            None,
        )

    @staticmethod
    def _require_main_thread() -> None:
        if sys.platform != "darwin":
            raise RuntimeError("discussion window is available only on macOS")
        if not NSThread.isMainThread():
            raise RuntimeError("discussion window operations require the main thread")
