# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>
#
# Part of "usage". Free software licensed under the GNU Affero General Public
# License v3.0 only; see the LICENSE file for full terms and the warranty disclaimer.

from __future__ import annotations

import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

import discussion_bridge
import discussion_cli
from discussion_bridge import DiscussionBridge, DiscussionBusyError, ParticipantSpec
from discussion_cli import (
    CLIAdapter,
    DetectionResult,
    Invocation,
    StreamError,
    StreamFailureReason,
)
from discussion_session import build_round1_prompt
from discussion_usage import TurnUsage

TERMINAL_STATUSES = {"COMPLETED", "CANCELLED", "FAILED"}


class FakeAdapter:
    def __init__(
        self,
        adapter_id: str,
        *,
        available: bool = True,
        supports_token_stream: bool = True,
    ) -> None:
        self.adapter_id = adapter_id
        self.available = available
        self.supports_token_stream = supports_token_stream
        self.detect_count = 0
        self.prompts: list[str] = []

    def detect(self) -> DetectionResult:
        self.detect_count += 1
        return DetectionResult(
            self.adapter_id,
            self.available,
            f"/fake/{self.adapter_id}" if self.available else None,
            "user_configured" if self.available else "not_found",
            None if self.available else f"{self.adapter_id} unavailable",
        )

    def build_invocation(self, prompt: str, model: str | None) -> Invocation:
        self.prompts.append(prompt)
        return Invocation(
            argv=(self.adapter_id, prompt),
            cwd="/fake/cwd",
            env_overrides={},
            timeout_seconds=1,
        )

    def parse_stdout_line(self, line: str) -> tuple[str | None, bool]:
        return line, True

    def take_final_text(self) -> str | None:
        return None

    def take_usage(self) -> TurnUsage | None:
        return None


class FakeRunner:
    def __init__(
        self,
        outcome: Callable[[str, int], str | Exception] | None = None,
    ) -> None:
        self.outcome = outcome or (lambda adapter_id, round_index: f"{adapter_id}-r{round_index}")
        self.calls: list[tuple[str, int]] = []
        self._lock = threading.Lock()

    def __call__(
        self,
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        round_index = _prompt_round(invocation.argv[-1])
        with self._lock:
            self.calls.append((adapter.adapter_id, round_index))
        result = self.outcome(adapter.adapter_id, round_index)
        if isinstance(result, Exception):
            on_error(str(result))
            return
        on_delta(result)
        on_done()


@pytest.fixture(autouse=True)
def _neutral_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        discussion_cli,
        "NEUTRAL_DISCUSSION_CWD",
        tmp_path / "neutral-discussion-cwd",
    )
    monkeypatch.setattr(discussion_bridge, "DISCUSSIONS_DIRECTORY", tmp_path / "discussions")


def _specs(*ids: str) -> list[ParticipantSpec]:
    return [
        ParticipantSpec(
            id=participant_id,
            label=participant_id.upper(),
            adapter_id=participant_id,
        )
        for participant_id in ids
    ]


def _bridge_with_adapters(
    ids: Sequence[str],
    *,
    unavailable: set[str] | None = None,
) -> tuple[DiscussionBridge, dict[str, FakeAdapter]]:
    unavailable = unavailable or set()
    adapters = {
        adapter_id: FakeAdapter(adapter_id, available=adapter_id not in unavailable)
        for adapter_id in ids
    }

    def factory(spec: ParticipantSpec) -> CLIAdapter:
        return adapters[spec.adapter_id]

    return DiscussionBridge(adapter_factory=factory), adapters


def _install_runner(monkeypatch: pytest.MonkeyPatch, runner: object) -> None:
    monkeypatch.setattr("discussion_bridge.run_streaming", runner)


def _wait_terminal(bridge: DiscussionBridge, timeout: float = 2.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = bridge.snapshot()
        if snapshot.get("status") in TERMINAL_STATUSES:
            return cast(dict[str, Any], snapshot)
        time.sleep(0.005)
    pytest.fail(f"discussion did not finish: {bridge.snapshot()}")


def _prompt_round(prompt: str) -> int:
    if "<<<TRANSCRIPT_BEGIN>>>" in prompt:
        return 3
    if "重新評估以下原始問題" in prompt:
        return 2
    return 1


def test_normal_three_participant_flow_and_event_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, adapters = _bridge_with_adapters(("a", "b", "c"))
    runner = FakeRunner()
    _install_runner(monkeypatch, runner)

    bridge.start("如何改善快取？", _specs("a", "b", "c"), moderator_id="b")
    snapshot = _wait_terminal(bridge)
    events = cast(list[dict[str, Any]], bridge.drain_events(500))

    assert snapshot["status"] == "COMPLETED"
    assert len(snapshot["turns"]) == 7
    assert Counter(round_index for _, round_index in runner.calls) == {1: 3, 2: 3, 3: 1}
    assert runner.calls[-1] == ("b", 3)
    event_sequences = [int(event["event_seq"]) for event in events]
    assert event_sequences == list(range(len(event_sequences)))
    assert events[0]["kind"] == "round_started"
    assert events[-1]["kind"] == "session_done"
    round_events = [
        event["payload"]["round_index"]
        for event in events
        if event["kind"] == "round_started"
    ]
    assert round_events == [1, 2]
    assert "參與者 A" in adapters["a"].prompts[1]
    assert "A（你在第一輪的發言）" not in adapters["a"].prompts[1]
    assert "a-r1" in adapters["b"].prompts[1]
    assert "共識" in adapters["b"].prompts[2]


def test_second_round_emits_consensus_count_without_changing_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _ = _bridge_with_adapters(("a", "b", "c"))
    runner = FakeRunner(
        lambda adapter_id, round_index: (
            {
                "a": "[Agree] 同意",
                "b": "［DISAGREE］ 不同意",
                "c": "未依格式",
            }[adapter_id]
            if round_index == 2
            else f"{adapter_id}-r{round_index}"
        )
    )
    _install_runner(monkeypatch, runner)

    bridge.start("問題", _specs("a", "b", "c"), include_summary=False)
    snapshot = _wait_terminal(bridge)
    events = bridge.drain_events(500)

    assert snapshot["status"] == "COMPLETED"
    assert snapshot["consensus_count"] == {
        "agree": 1,
        "disagree": 1,
        "alternative": 0,
        "unparsed": 1,
    }
    consensus_events = [
        event for event in events if event["kind"] == "consensus_counted"
    ]
    assert len(consensus_events) == 1
    assert consensus_events[0]["payload"] == snapshot["consensus_count"]
    assert Counter(round_index for _, round_index in runner.calls) == {1: 3, 2: 3}


def test_single_participant_skips_round2_and_moderator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _ = _bridge_with_adapters(("solo",))
    runner = FakeRunner()
    _install_runner(monkeypatch, runner)

    bridge.start("問題", _specs("solo"))
    snapshot = _wait_terminal(bridge)

    assert snapshot["status"] == "COMPLETED"
    assert runner.calls == [("solo", 1)]
    assert [turn["round_index"] for turn in snapshot["turns"]] == [1]


def test_one_round_creates_only_independent_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _ = _bridge_with_adapters(("a", "b"))
    runner = FakeRunner()
    _install_runner(monkeypatch, runner)

    bridge.start("問題", _specs("a", "b"), total_rounds=1, include_summary=False)
    snapshot = _wait_terminal(bridge)

    assert [turn["round_index"] for turn in snapshot["turns"]] == [1, 1]
    assert Counter(round_index for _, round_index in runner.calls) == {1: 2}


def test_three_rounds_keep_peer_review_anonymous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = [
        ParticipantSpec("claude", "Claude", "claude"),
        ParticipantSpec("codex", "Codex", "codex"),
    ]
    bridge, adapters = _bridge_with_adapters(("claude", "codex"))
    runner = FakeRunner()
    _install_runner(monkeypatch, runner)

    bridge.start("問題", specs, total_rounds=3, include_summary=False)
    snapshot = _wait_terminal(bridge)

    assert {turn["round_index"] for turn in snapshot["turns"]} == {1, 2, 3}
    for prompt in adapters["claude"].prompts[1:]:
        assert "參與者 A" in prompt
        assert "Claude" not in prompt
        assert "Codex" not in prompt


def test_summary_can_be_disabled_without_preventing_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _ = _bridge_with_adapters(("a", "b"))
    runner = FakeRunner()
    _install_runner(monkeypatch, runner)

    bridge.start("問題", _specs("a", "b"), include_summary=False)
    snapshot = _wait_terminal(bridge)

    assert snapshot["status"] == "COMPLETED"
    assert all(round_index != 3 for _, round_index in runner.calls)


def test_round2_is_anonymous_but_moderator_transcript_uses_real_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = [
        ParticipantSpec("claude", "Claude", "claude"),
        ParticipantSpec("codex", "Codex", "codex"),
        ParticipantSpec("agy", "Antigravity", "agy"),
    ]
    bridge, adapters = _bridge_with_adapters(("claude", "codex", "agy"))
    _install_runner(monkeypatch, FakeRunner())

    bridge.start("問題", specs, moderator_id="claude")
    _wait_terminal(bridge)

    round2 = adapters["claude"].prompts[1]
    transcript = adapters["claude"].prompts[2]
    assert "參與者 A" in round2
    assert not any(label in round2 for label in ("Claude", "Codex", "Antigravity"))
    assert "你在第一輪的發言" not in round2
    assert all(label in transcript for label in ("Claude", "Codex", "Antigravity"))


def test_each_participant_receives_only_its_own_persona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = [
        ParticipantSpec(
            "claude",
            "Claude",
            "claude",
            persona_prompt="法律角色提示",
            persona_label="合約審閱",
        ),
        ParticipantSpec(
            "codex",
            "Codex",
            "codex",
            persona_prompt="工程角色提示",
            persona_label="架構審查",
        ),
    ]
    bridge, adapters = _bridge_with_adapters(("claude", "codex"))
    _install_runner(monkeypatch, FakeRunner())

    bridge.start("問題", specs, include_summary=False)
    snapshot = _wait_terminal(bridge)

    assert all("法律角色提示" in prompt for prompt in adapters["claude"].prompts)
    assert all("工程角色提示" not in prompt for prompt in adapters["claude"].prompts)
    assert all("工程角色提示" in prompt for prompt in adapters["codex"].prompts)
    assert all("法律角色提示" not in prompt for prompt in adapters["codex"].prompts)
    assert not any(
        label in prompt
        for adapter in adapters.values()
        for prompt in adapter.prompts
        for label in ("合約審閱", "架構審查")
    )
    participants = cast(list[dict[str, Any]], snapshot["participants"])
    assert [participant["persona_label"] for participant in participants] == [
        "合約審閱",
        "架構審查",
    ]


def test_nonzero_exit_retries_once_then_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def runner(
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            on_delta("殘缺")
            on_error(StreamError("exit 1", StreamFailureReason.NONZERO_EXIT))
            return
        on_delta("成功")
        on_done()

    bridge, _ = _bridge_with_adapters(("solo",))
    _install_runner(monkeypatch, runner)
    bridge.start("問題", _specs("solo"))
    snapshot = _wait_terminal(bridge)

    assert calls == 2
    assert snapshot["turns"][0]["status"] == "DONE"
    assert snapshot["turns"][0]["text"] == "成功"


def test_cancelled_turn_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    started = threading.Event()

    def runner(
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        nonlocal calls
        calls += 1
        assert cancel_event is not None
        started.set()
        cancel_event.wait(1)
        on_cancelled()

    bridge, _ = _bridge_with_adapters(("solo",))
    _install_runner(monkeypatch, runner)
    bridge.start("問題", _specs("solo"))
    assert started.wait(1)
    bridge.stop()
    snapshot = _wait_terminal(bridge)

    assert calls == 1
    assert snapshot["status"] == "CANCELLED"


def test_completed_session_is_archived_as_json_and_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_dir = tmp_path / "archive"
    monkeypatch.setattr(discussion_bridge, "DISCUSSIONS_DIRECTORY", archive_dir)
    bridge, _ = _bridge_with_adapters(("a", "b"))
    _install_runner(monkeypatch, FakeRunner())
    session_id = bridge.start("存檔主題", _specs("a", "b"))
    _wait_terminal(bridge)

    assert (archive_dir / f"{session_id}.json").is_file()
    markdown = (archive_dir / f"{session_id}.md").read_text(encoding="utf-8")
    assert "存檔主題" in markdown
    assert "## 主持人總結" in markdown


def test_archive_failure_does_not_prevent_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenDirectory:
        def mkdir(self, **kwargs: object) -> None:
            raise OSError("read-only")

    monkeypatch.setattr(discussion_bridge, "DISCUSSIONS_DIRECTORY", BrokenDirectory())
    bridge, _ = _bridge_with_adapters(("solo",))
    _install_runner(monkeypatch, FakeRunner())
    bridge.start("問題", _specs("solo"))

    assert _wait_terminal(bridge)["status"] == "COMPLETED"


def test_all_round1_failures_fail_session_without_round2_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _ = _bridge_with_adapters(("a", "b"))
    runner = FakeRunner(lambda adapter_id, round_index: RuntimeError(f"{adapter_id} quota"))
    _install_runner(monkeypatch, runner)

    bridge.start("問題", _specs("a", "b"))
    snapshot = _wait_terminal(bridge)

    assert snapshot["status"] == "FAILED"
    assert Counter(round_index for _, round_index in runner.calls) == {1: 2}
    assert all(turn["status"] == "FAILED" for turn in snapshot["turns"])


def test_round1_partial_failure_preserves_error_and_survivors_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def outcome(adapter_id: str, round_index: int) -> str | Exception:
        if adapter_id == "a" and round_index == 1:
            return RuntimeError("原始錯誤：登入失敗")
        return f"{adapter_id}-r{round_index}"

    bridge, _ = _bridge_with_adapters(("a", "b", "c"))
    runner = FakeRunner(outcome)
    _install_runner(monkeypatch, runner)

    bridge.start("問題", _specs("a", "b", "c"))
    snapshot = _wait_terminal(bridge)
    turns = snapshot["turns"]

    assert snapshot["status"] == "COMPLETED"
    assert Counter(runner.calls) == {
        ("a", 1): 1,
        ("b", 1): 1,
        ("c", 1): 1,
        ("b", 2): 1,
        ("c", 2): 1,
        ("b", 3): 1,
    }
    failed = next(turn for turn in turns if turn["participant_id"] == "a")
    assert failed["error"] == "原始錯誤：登入失敗"


def test_three_participants_with_one_round1_survivor_skip_later_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def outcome(adapter_id: str, round_index: int) -> str | Exception:
        if adapter_id != "a":
            return RuntimeError(f"{adapter_id} round 1 failed")
        return "a-r1"

    bridge, _ = _bridge_with_adapters(("a", "b", "c"))
    runner = FakeRunner(outcome)
    _install_runner(monkeypatch, runner)

    bridge.start("問題", _specs("a", "b", "c"))
    snapshot = _wait_terminal(bridge)

    assert snapshot["status"] == "COMPLETED"
    assert len(runner.calls) == 3
    assert Counter(round_index for _, round_index in runner.calls) == {1: 3}
    assert all(turn["round_index"] == 1 for turn in snapshot["turns"])


def test_failed_designated_moderator_falls_back_to_survivor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def outcome(adapter_id: str, round_index: int) -> str | Exception:
        if adapter_id == "a" and round_index == 2:
            return RuntimeError("round 2 failed")
        return f"{adapter_id}-r{round_index}"

    bridge, _ = _bridge_with_adapters(("a", "b"))
    runner = FakeRunner(outcome)
    _install_runner(monkeypatch, runner)

    bridge.start("問題", _specs("a", "b"), moderator_id="a")
    snapshot = _wait_terminal(bridge)

    assert snapshot["status"] == "COMPLETED"
    assert runner.calls[-1] == ("b", 3)


def test_moderator_failure_is_preserved_on_summary_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def outcome(adapter_id: str, round_index: int) -> str | Exception:
        if round_index == 3:
            return RuntimeError("主持人額度耗盡")
        return f"{adapter_id}-r{round_index}"

    bridge, _ = _bridge_with_adapters(("a", "b"))
    _install_runner(monkeypatch, FakeRunner(outcome))

    bridge.start("問題", _specs("a", "b"), moderator_id="a")
    snapshot = _wait_terminal(bridge)
    summary_turn = next(
        turn for turn in snapshot["turns"] if turn["round_index"] == 3
    )

    assert snapshot["status"] == "COMPLETED"
    assert summary_turn["status"] == "FAILED"
    assert summary_turn["error"] == "主持人額度耗盡"


def test_unspecified_moderator_uses_first_survivor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _ = _bridge_with_adapters(("a", "b"))
    runner = FakeRunner()
    _install_runner(monkeypatch, runner)

    bridge.start("問題", _specs("a", "b"))
    snapshot = _wait_terminal(bridge)

    assert snapshot["status"] == "COMPLETED"
    assert runner.calls[-1] == ("a", 3)


def test_unavailable_participant_fails_without_runner_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _ = _bridge_with_adapters(("a", "b"), unavailable={"a"})
    runner = FakeRunner()
    _install_runner(monkeypatch, runner)

    bridge.start("問題", _specs("a", "b"))
    snapshot = _wait_terminal(bridge)

    assert snapshot["status"] == "COMPLETED"
    assert ("a", 1) not in runner.calls
    failed = next(
        turn
        for turn in snapshot["turns"]
        if turn["participant_id"] == "a"
    )
    assert failed["status"] == "FAILED"
    assert failed["error"] == "a unavailable"


def test_no_round2_survivor_completes_without_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def outcome(adapter_id: str, round_index: int) -> str | Exception:
        if round_index == 2:
            return RuntimeError(f"{adapter_id} round 2 failed")
        return f"{adapter_id}-r{round_index}"

    bridge, _ = _bridge_with_adapters(("a", "b"))
    runner = FakeRunner(outcome)
    _install_runner(monkeypatch, runner)

    bridge.start("問題", _specs("a", "b"), moderator_id="a")
    snapshot = _wait_terminal(bridge)

    assert snapshot["status"] == "COMPLETED"
    assert not any(round_index == 3 for _, round_index in runner.calls)
    assert len(snapshot["turns"]) == 4


def test_start_reentry_raises_busy_error(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocking_runner(
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        started.set()
        release.wait(1)
        on_done()

    bridge, _ = _bridge_with_adapters(("a", "b"))
    _install_runner(monkeypatch, blocking_runner)
    bridge.start("第一場", _specs("a", "b"))
    assert started.wait(1)

    with pytest.raises(DiscussionBusyError):
        bridge.start("第二場", _specs("a", "b"))

    bridge.stop()
    release.set()
    bridge.shutdown(1)


def test_blank_topic_rejected_without_detection_or_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory_calls = 0
    runner_calls = 0

    def factory(spec: ParticipantSpec) -> CLIAdapter:
        nonlocal factory_calls
        factory_calls += 1
        return FakeAdapter(spec.adapter_id)

    def runner(*args: object) -> None:
        nonlocal runner_calls
        runner_calls += 1

    bridge = DiscussionBridge(adapter_factory=factory)
    _install_runner(monkeypatch, runner)

    with pytest.raises(ValueError, match="blank"):
        bridge.start("   ", _specs("a"))

    assert factory_calls == 0
    assert runner_calls == 0


def test_stop_cancels_immediately_and_blocks_late_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()

    def late_callback_runner(
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        started.set()
        assert cancel_event is not None
        cancel_event.wait(1)
        on_delta("late text")
        on_done()

    bridge, _ = _bridge_with_adapters(("a", "b"))
    _install_runner(monkeypatch, late_callback_runner)
    bridge.start("問題", _specs("a", "b"))
    assert started.wait(1)

    bridge.stop()
    first_events = cast(list[dict[str, Any]], bridge.drain_events(500))
    bridge.shutdown(1)
    time.sleep(0.02)

    assert bridge.snapshot()["status"] == "CANCELLED"
    assert first_events[-1]["payload"]["status"] == "CANCELLED"
    assert bridge.drain_events(500) == []
    assert all("late text" not in str(event) for event in first_events)


def test_stop_marks_incomplete_turns_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()

    def stuck_runner(
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        started.set()
        assert cancel_event is not None
        cancel_event.wait(1)

    bridge, _ = _bridge_with_adapters(("a", "b"))
    _install_runner(monkeypatch, stuck_runner)
    bridge.start("問題", _specs("a", "b"))
    assert started.wait(1)

    bridge.stop()
    events = cast(list[dict[str, Any]], bridge.drain_events(500))
    bridge.shutdown(1)
    time.sleep(0.02)

    cancelled = [event for event in events if event["kind"] == "turn_cancelled"]
    assert len(cancelled) == 2
    assert all(event["turn_id"] for event in cancelled)
    assert events[-1]["kind"] == "session_done"
    assert events[-1]["payload"]["status"] == "CANCELLED"


def test_clear_refuses_while_running(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()

    def stuck_runner(
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        started.set()
        assert cancel_event is not None
        cancel_event.wait(1)

    bridge, _ = _bridge_with_adapters(("a", "b"))
    _install_runner(monkeypatch, stuck_runner)
    bridge.start("問題", _specs("a", "b"))
    assert started.wait(1)

    assert bridge.clear() == {"status": "busy"}
    # busy clear must leave the running session intact (not torn down)
    assert bridge.snapshot().get("status") in {"ROUND1_RUNNING", "CANCELLING"}

    bridge.shutdown(1)


def test_clear_when_idle_empties_finished_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _ = _bridge_with_adapters(("a", "b"))
    _install_runner(monkeypatch, FakeRunner())
    bridge.start("問題", _specs("a", "b"))
    snapshot = _wait_terminal(bridge)

    assert snapshot["status"] == "COMPLETED"
    assert bridge.snapshot().get("turns")

    assert bridge.clear() == {"status": "ok"}
    assert bridge.snapshot() == {}
    assert bridge.drain_events(500) == []


def test_stop_without_session_is_safe_noop() -> None:
    bridge = DiscussionBridge()

    bridge.stop()

    assert bridge.snapshot() == {}
    assert bridge.drain_events() == []


@pytest.mark.parametrize("working_directory", [None, "project"])
def test_start_passes_project_mode_to_adapter_specs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    working_directory: str | None,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    captured: list[ParticipantSpec] = []
    adapter = FakeAdapter("claude")

    def factory(spec: ParticipantSpec) -> CLIAdapter:
        captured.append(spec)
        return adapter

    bridge = DiscussionBridge(adapter_factory=factory)
    _install_runner(monkeypatch, FakeRunner())
    selected = str(project) if working_directory is not None else None

    bridge.start("問題", _specs("claude"), working_directory=selected)
    snapshot = _wait_terminal(bridge)

    assert len(captured) == 1
    assert captured[0].cwd == (str(project.resolve()) if selected else None)
    assert captured[0].read_only is (selected is not None)
    assert snapshot["working_directory"] == (
        str(project.resolve()) if selected else None
    )


@pytest.mark.parametrize("kind", ["blank", "missing", "file"])
def test_start_rejects_invalid_working_directory_before_adapter_creation(
    tmp_path: Path,
    kind: str,
) -> None:
    path = tmp_path / kind
    if kind == "file":
        path.write_text("not a directory")
    value = "" if kind == "blank" else str(path)
    factory_calls = 0

    def factory(spec: ParticipantSpec) -> CLIAdapter:
        nonlocal factory_calls
        factory_calls += 1
        return FakeAdapter(spec.adapter_id)

    bridge = DiscussionBridge(adapter_factory=factory)

    with pytest.raises(ValueError, match="working directory"):
        bridge.start("問題", _specs("claude"), working_directory=value)

    assert factory_calls == 0
    assert bridge.snapshot() == {}


def test_shutdown_is_bounded_when_runner_is_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def stuck_runner(
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        started.set()
        release.wait(2)
        on_cancelled()

    bridge, _ = _bridge_with_adapters(("a", "b"))
    _install_runner(monkeypatch, stuck_runner)
    bridge.start("問題", _specs("a", "b"))
    assert started.wait(1)

    before = time.monotonic()
    bridge.shutdown(0.05)
    elapsed = time.monotonic() - before
    release.set()

    assert elapsed < 0.2
    assert bridge.snapshot()["status"] == "CANCELLED"


def test_listener_notified_only_when_queue_becomes_nonempty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def controlled_runner(
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        started.set()
        release.wait(1)
        on_delta("answer")
        on_done()

    bridge, _ = _bridge_with_adapters(("solo",))
    notifications = 0

    def listener() -> None:
        nonlocal notifications
        notifications += 1

    bridge.set_event_listener(listener)
    _install_runner(monkeypatch, controlled_runner)
    bridge.start("問題", _specs("solo"))
    assert started.wait(1)
    assert notifications == 1

    assert bridge.drain_events(500)
    release.set()
    _wait_terminal(bridge)

    assert notifications == 2


def test_listener_exception_does_not_break_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _ = _bridge_with_adapters(("solo",))
    _install_runner(monkeypatch, FakeRunner())

    def broken_listener() -> None:
        raise RuntimeError("UI unavailable")

    bridge.set_event_listener(broken_listener)
    bridge.start("問題", _specs("solo"))

    assert _wait_terminal(bridge)["status"] == "COMPLETED"


def test_drain_events_respects_max_count_and_removes_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _ = _bridge_with_adapters(("solo",))
    _install_runner(monkeypatch, FakeRunner())
    bridge.start("問題", _specs("solo"))
    _wait_terminal(bridge)

    first = bridge.drain_events(2)
    rest = bridge.drain_events(500)

    assert len(first) == 2
    assert rest
    assert bridge.drain_events(1) == []


def test_delta_coalescing_preserves_all_text_and_flushes_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fragmented_runner(
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        for part in ("甲", "乙", "丙"):
            on_delta(part)
        on_done()

    bridge, _ = _bridge_with_adapters(("solo",))
    _install_runner(monkeypatch, fragmented_runner)
    bridge.start("問題", _specs("solo"))
    snapshot = _wait_terminal(bridge)
    events = cast(list[dict[str, Any]], bridge.drain_events(500))

    assert snapshot["turns"][0]["text"] == "甲乙丙"
    deltas = [event["payload"]["text"] for event in events if event["kind"] == "text_delta"]
    assert "".join(str(delta) for delta in deltas) == "甲乙丙"
    assert len(deltas) == 1


def test_concurrent_process_limit_never_exceeds_four(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    peak = 0
    active_lock = threading.Lock()

    def measured_runner(
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        nonlocal active, peak
        with active_lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        on_delta(adapter.adapter_id)
        on_done()
        with active_lock:
            active -= 1

    ids = tuple(f"p{index}" for index in range(9))
    bridge, _ = _bridge_with_adapters(ids)
    _install_runner(monkeypatch, measured_runner)
    bridge.start("問題", _specs(*ids))
    _wait_terminal(bridge, timeout=3)

    assert peak == discussion_bridge.MAX_CONCURRENT_PROCESSES


def test_participants_are_redetected_for_every_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, adapters = _bridge_with_adapters(("solo",))
    _install_runner(monkeypatch, FakeRunner())

    bridge.start("第一場", _specs("solo"))
    _wait_terminal(bridge)
    bridge.start("第二場", _specs("solo"))
    _wait_terminal(bridge)

    assert adapters["solo"].detect_count == 2


@pytest.mark.skipif(
    sys.platform == "win32", reason="login_shell source hardcodes the POSIX /bin/zsh shell"
)
def test_custom_argv_and_login_shell_sources_build_safe_invocations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "custom"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    captured: list[tuple[str, ...]] = []
    parsed_lines: list[tuple[str | None, bool]] = []

    def capture_runner(
        adapter: CLIAdapter,
        invocation: Invocation,
        on_delta: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
        on_final_text: Callable[[str], None] | None = None,
        on_usage: Callable[[TurnUsage], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        captured.append(invocation.argv)
        parsed_lines.append(adapter.parse_stdout_line("first line\n"))
        on_delta("answer")
        on_done()

    specs = [
        ParticipantSpec(
            "argv",
            "Argv",
            "custom-argv",
            source="argv",
            executable=str(executable),
            args_before_prompt=("--before",),
            args_after_prompt=("--after",),
        ),
        ParticipantSpec(
            "shell",
            "Shell",
            "custom-shell",
            source="login_shell",
            login_shell_script='tool "$1"',
            login_shell_opt_in=True,
        ),
    ]
    bridge = DiscussionBridge()
    _install_runner(monkeypatch, capture_runner)
    bridge.start("安全提示", specs)
    _wait_terminal(bridge)

    argv_call = next(argv for argv in captured if argv[0] == str(executable))
    shell_call = next(argv for argv in captured if argv[0] == "/bin/zsh")
    assert argv_call[1:] == ("--before", build_round1_prompt_text(), "--after")
    assert shell_call[:5] == (
        "/bin/zsh",
        "-lic",
        'tool "$1"',
        "usage-discussion",
        build_round1_prompt_text(),
    )
    assert parsed_lines
    assert all(parsed == ("first line\n", False) for parsed in parsed_lines)


def build_round1_prompt_text() -> str:
    return build_round1_prompt("安全提示")


def test_build_attachment_block_appends_existing_files_only(
    tmp_path: Path,
) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"x")
    missing = str(tmp_path / "nope.png")

    block = discussion_bridge.build_attachment_block([str(image), missing], "en")

    assert block
    assert str(image.resolve()) in block
    assert missing not in block
    # header text is sourced from i18n, not hardcoded in the prompt
    assert "read the following image" in block


def test_build_attachment_block_empty_when_no_existing_files(
    tmp_path: Path,
) -> None:
    assert discussion_bridge.build_attachment_block([], "en") == ""
    assert (
        discussion_bridge.build_attachment_block(
            [str(tmp_path / "missing.png")], "en"
        )
        == ""
    )


def test_start_appends_attachment_paths_to_prompt_and_keeps_topic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"x")
    bridge, adapters = _bridge_with_adapters(("solo",))
    _install_runner(monkeypatch, FakeRunner())

    bridge.start("看圖回答", _specs("solo"), attachments=[str(image)])
    snapshot = _wait_terminal(bridge)

    assert snapshot["status"] == "COMPLETED"
    assert snapshot["topic"] == "看圖回答"
    assert str(image.resolve()) in adapters["solo"].prompts[0]
    assert "看圖回答" in adapters["solo"].prompts[0]


def test_start_skips_missing_attachments_and_still_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bridge, adapters = _bridge_with_adapters(("solo",))
    _install_runner(monkeypatch, FakeRunner())
    missing = str(tmp_path / "nope.png")

    bridge.start("問題", _specs("solo"), attachments=[missing])
    snapshot = _wait_terminal(bridge)

    assert snapshot["status"] == "COMPLETED"
    assert missing not in adapters["solo"].prompts[0]


def test_start_propagates_attachment_dir_to_specs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"x")
    captured: list[ParticipantSpec] = []

    def factory(spec: ParticipantSpec) -> CLIAdapter:
        captured.append(spec)
        return FakeAdapter(spec.adapter_id)

    bridge = DiscussionBridge(adapter_factory=factory)
    _install_runner(monkeypatch, FakeRunner())

    bridge.start("看圖", _specs("solo"), attachments=[str(image)])
    _wait_terminal(bridge)

    assert len(captured) == 1
    assert captured[0].extra_read_dirs == (str(image.resolve().parent),)


def test_start_leaves_extra_read_dirs_empty_without_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[ParticipantSpec] = []

    def factory(spec: ParticipantSpec) -> CLIAdapter:
        captured.append(spec)
        return FakeAdapter(spec.adapter_id)

    bridge = DiscussionBridge(adapter_factory=factory)
    _install_runner(monkeypatch, FakeRunner())

    bridge.start("問題", _specs("solo"))
    _wait_terminal(bridge)

    assert len(captured) == 1
    assert captured[0].extra_read_dirs == ()
