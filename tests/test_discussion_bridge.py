# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>
#
# Part of "usage". Free software licensed under the GNU Affero General Public
# License v3.0 only; see the LICENSE file for full terms and the warranty disclaimer.

from __future__ import annotations

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
from discussion_cli import CLIAdapter, DetectionResult, Invocation
from discussion_session import build_round1_prompt

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
        cancel_event: threading.Event,
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
    assert "A（你在第一輪的發言）" in adapters["a"].prompts[1]
    assert "a-r1" in adapters["b"].prompts[1]
    assert "共識" in adapters["b"].prompts[2]


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
        cancel_event: threading.Event,
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
        cancel_event: threading.Event,
    ) -> None:
        started.set()
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
        cancel_event: threading.Event,
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
        cancel_event: threading.Event,
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
        cancel_event: threading.Event,
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
        cancel_event: threading.Event,
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
        cancel_event: threading.Event,
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
