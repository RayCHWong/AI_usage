# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>
#
# Part of "usage". Free software licensed under the GNU Affero General Public
# License v3.0 only; see the LICENSE file for full terms and the warranty disclaimer.

"""PyObjC-free orchestration for one AI council discussion at a time."""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from discussion_cli import (
    DEFAULT_TIMEOUT_SECONDS,
    ClaudeAdapter,
    CLIAdapter,
    CodexAdapter,
    DetectionResult,
    GeminiAdapter,
    Invocation,
    build_argv_invocation,
    build_login_shell_invocation,
    resolve_neutral_working_directory,
    run_streaming,
)
from discussion_session import (
    DiscussionEvent,
    DiscussionSession,
    Participant,
    SessionStatus,
    build_moderator_prompt,
    build_round1_prompt,
    build_round2_prompt,
)

MAX_CONCURRENT_PROCESSES = 4
DELTA_FLUSH_CHARS = 128
DELTA_FLUSH_SECONDS = 0.05

ParticipantSource = Literal["builtin", "argv", "login_shell"]
AdapterFactory = Callable[["ParticipantSpec"], CLIAdapter]


class DiscussionBusyError(RuntimeError):
    """Raised when start is called while the current session is still running."""


@dataclass(frozen=True)
class ParticipantSpec:
    id: str
    label: str
    adapter_id: str
    model: str | None = None
    source: ParticipantSource = "builtin"
    executable: str | None = None
    args_before_prompt: tuple[str, ...] = ()
    args_after_prompt: tuple[str, ...] = ()
    login_shell_script: str | None = None
    login_shell_opt_in: bool = False
    cwd: str | None = None
    env_overrides: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    supports_token_stream: bool = False


@dataclass(frozen=True)
class _ResolvedParticipant:
    spec: ParticipantSpec
    adapter: CLIAdapter | None
    detection: DetectionResult


@dataclass(frozen=True)
class _TurnResult:
    participant: _ResolvedParticipant
    turn_id: str | None
    success: bool
    text: str
    error: str | None


class _DeltaAccumulator:
    def __init__(self) -> None:
        self._parts: list[str] = []
        self._length = 0
        self._last_flush = time.monotonic()
        self._lock = threading.Lock()

    def add(self, text: str) -> str:
        with self._lock:
            self._parts.append(text)
            self._length += len(text)
            now = time.monotonic()
            if (
                self._length < DELTA_FLUSH_CHARS
                and now - self._last_flush < DELTA_FLUSH_SECONDS
            ):
                return ""
            return self._take_locked(now)

    def flush(self) -> str:
        with self._lock:
            return self._take_locked(time.monotonic())

    def _take_locked(self, now: float) -> str:
        if not self._parts:
            return ""
        value = "".join(self._parts)
        self._parts.clear()
        self._length = 0
        self._last_flush = now
        return value


class _CustomLineAdapter:
    def __init__(self, spec: ParticipantSpec) -> None:
        self.adapter_id = spec.adapter_id
        self.supports_token_stream = spec.supports_token_stream
        self._spec = spec

    def detect(self) -> DetectionResult:
        if self._spec.source == "login_shell":
            shell = Path("/bin/zsh")
            available = shell.is_file() and os.access(shell, os.X_OK)
            return DetectionResult(
                self.adapter_id,
                available,
                str(shell) if available else None,
                "user_configured" if available else "not_found",
                None if available else "/bin/zsh is missing or not executable",
            )
        executable = self._spec.executable
        if not executable:
            return DetectionResult(
                self.adapter_id,
                False,
                None,
                "not_found",
                "custom executable is required",
            )
        path = Path(executable)
        if path.is_absolute():
            available = path.is_file() and os.access(path, os.X_OK)
            return DetectionResult(
                self.adapter_id,
                available,
                str(path),
                "user_configured",
                None if available else "custom executable is missing or not executable",
            )
        found = shutil.which(executable)
        return DetectionResult(
            self.adapter_id,
            found is not None,
            found,
            "which" if found is not None else "not_found",
            None if found is not None else f"custom executable not found: {executable}",
        )

    def build_invocation(self, prompt: str, model: str | None) -> Invocation:
        cwd = self._spec.cwd or resolve_neutral_working_directory()
        if self._spec.source == "login_shell":
            script = self._spec.login_shell_script
            if script is None:
                raise ValueError("login-shell command requires login_shell_script")
            return build_login_shell_invocation(
                script,
                prompt,
                opt_in=self._spec.login_shell_opt_in,
                cwd=cwd,
                env_overrides=self._spec.env_overrides,
                timeout_seconds=self._spec.timeout_seconds,
            )
        detection = self.detect()
        if not detection.available or detection.path is None:
            raise ValueError(detection.error or "custom executable is unavailable")
        return build_argv_invocation(
            detection.path,
            self._spec.args_before_prompt,
            self._spec.args_after_prompt,
            prompt,
            cwd=cwd,
            env_overrides=self._spec.env_overrides,
            timeout_seconds=self._spec.timeout_seconds,
        )

    def parse_stdout_line(self, line: str) -> tuple[str | None, bool]:
        return (line, False) if line else (None, False)


class DiscussionBridge:
    def __init__(self, adapter_factory: AdapterFactory | None = None) -> None:
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._session: DiscussionSession | None = None
        self._worker: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._state_lock = threading.Lock()
        self._event_order_lock = threading.RLock()
        self._event_lock = threading.Lock()
        self._events: deque[DiscussionEvent] = deque()
        self._event_listener: Callable[[], None] | None = None
        self._callbacks_enabled = True

    def detect_participants(self) -> list[DetectionResult]:
        return [
            ClaudeAdapter().detect(),
            CodexAdapter().detect(),
            GeminiAdapter().detect(),
        ]

    def start(
        self,
        topic: str,
        participants: Sequence[ParticipantSpec],
        moderator_id: str | None = None,
    ) -> str:
        normalized_topic = topic.strip()
        if not normalized_topic:
            raise ValueError("topic must not be blank")
        specs = tuple(participants)
        if not specs:
            raise ValueError("at least one participant is required")
        participant_models = [
            Participant(
                id=spec.id,
                label=spec.label,
                adapter_id=spec.adapter_id,
                model=spec.model,
                is_moderator=spec.id == moderator_id,
            )
            for spec in specs
        ]
        session = DiscussionSession(normalized_topic, participant_models)
        cancel_event = threading.Event()
        with self._state_lock:
            if self._worker is not None and self._worker.is_alive():
                raise DiscussionBusyError("a discussion session is already running")
            self._session = session
            self._cancel_event = cancel_event
            self._callbacks_enabled = True
            with self._event_lock:
                self._events.clear()
            session.transition(SessionStatus.PREPARING)
            worker = threading.Thread(
                target=self._run_session,
                args=(session, specs, moderator_id, cancel_event),
                name=f"discussion-session-{session.session_id}",
                daemon=True,
            )
            self._worker = worker
            worker.start()
        return session.session_id

    def stop(self) -> None:
        with self._state_lock:
            session = self._session
            cancel_event = self._cancel_event
        if session is None or cancel_event is None:
            return
        with self._event_order_lock:
            if session.status not in {
                SessionStatus.PREPARING,
                SessionStatus.ROUND1_RUNNING,
                SessionStatus.ROUND2_RUNNING,
                SessionStatus.SUMMARIZING,
            }:
                return
            cancel_event.set()
            session.transition(SessionStatus.CANCELLING)
            event = session.transition(SessionStatus.CANCELLED)
            if event is not None:
                self._enqueue_event_locked(event)

    def snapshot(self) -> dict[str, object]:
        with self._state_lock:
            session = self._session
        if session is None:
            return {}
        return session.snapshot()

    def drain_events(self, max_count: int = 50) -> list[dict[str, object]]:
        if max_count <= 0:
            return []
        drained: list[dict[str, object]] = []
        with self._event_lock:
            for _ in range(min(max_count, len(self._events))):
                drained.append(asdict(self._events.popleft()))
        return drained

    def set_event_listener(self, callback: Callable[[], None] | None) -> None:
        with self._event_lock:
            self._event_listener = callback

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        self.stop()
        with self._state_lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout=max(0.0, timeout_seconds))
        with self._event_order_lock, self._event_lock:
            self._callbacks_enabled = False
            self._event_listener = None

    def _run_session(
        self,
        session: DiscussionSession,
        specs: tuple[ParticipantSpec, ...],
        moderator_id: str | None,
        cancel_event: threading.Event,
    ) -> None:
        try:
            resolved = self._resolve_participants(specs)
            if cancel_event.is_set():
                return
            self._transition(session, cancel_event, SessionStatus.ROUND1_RUNNING)
            round1_prompt = build_round1_prompt(session.topic)
            round1 = self._run_round(
                session,
                resolved,
                1,
                lambda participant: round1_prompt,
                cancel_event,
            )
            if cancel_event.is_set():
                return
            round1_survivors = [result for result in round1 if result.success]
            if not round1_survivors:
                self._transition(
                    session,
                    cancel_event,
                    SessionStatus.FAILED,
                    error="all participants failed in round 1",
                )
                return
            if len(round1_survivors) < 2:
                self._transition(session, cancel_event, SessionStatus.COMPLETED)
                return

            self._transition(session, cancel_event, SessionStatus.ROUND2_RUNNING)
            round1_answers = [
                (result.participant.spec.label, result.text) for result in round1_survivors
            ]

            def round2_prompt(participant: _ResolvedParticipant) -> str:
                labelled_answers = [
                    (
                        label
                        + (
                            "（你在第一輪的發言）"
                            if result.participant is participant
                            else ""
                        ),
                        text,
                    )
                    for (label, text), result in zip(round1_answers, round1_survivors, strict=True)
                ]
                return build_round2_prompt(session.topic, labelled_answers)

            round2 = self._run_round(
                session,
                [result.participant for result in round1_survivors],
                2,
                round2_prompt,
                cancel_event,
            )
            if cancel_event.is_set():
                return
            round2_survivors = [result for result in round2 if result.success]
            if not round2_survivors:
                self._transition(session, cancel_event, SessionStatus.COMPLETED)
                return

            moderator = _select_moderator(round2_survivors, moderator_id)
            if moderator is None:
                self._transition(session, cancel_event, SessionStatus.COMPLETED)
                return
            self._transition(session, cancel_event, SessionStatus.SUMMARIZING)
            transcript = _build_transcript(session)
            self._run_turn(
                session,
                moderator.participant,
                3,
                build_moderator_prompt(transcript),
                cancel_event,
            )
            if cancel_event.is_set():
                return
            self._transition(session, cancel_event, SessionStatus.COMPLETED)
        except Exception as exc:
            if cancel_event.is_set():
                return
            try:
                self._transition(
                    session,
                    cancel_event,
                    SessionStatus.FAILED,
                    error=str(exc),
                )
            except Exception:
                return

    def _resolve_participants(
        self,
        specs: tuple[ParticipantSpec, ...],
    ) -> list[_ResolvedParticipant]:
        resolved: list[_ResolvedParticipant] = []
        for spec in specs:
            try:
                adapter = self._adapter_factory(spec)
                detection = adapter.detect()
            except Exception as exc:
                adapter = None
                detection = DetectionResult(
                    spec.adapter_id,
                    False,
                    None,
                    "not_found",
                    str(exc),
                )
            resolved.append(_ResolvedParticipant(spec, adapter, detection))
        return resolved

    def _run_round(
        self,
        session: DiscussionSession,
        participants: Sequence[_ResolvedParticipant],
        round_index: int,
        prompt_factory: Callable[[_ResolvedParticipant], str],
        cancel_event: threading.Event,
    ) -> list[_TurnResult]:
        results: list[_TurnResult | None] = [None] * len(participants)
        results_lock = threading.Lock()
        semaphore = threading.Semaphore(MAX_CONCURRENT_PROCESSES)

        def run_one(index: int, participant: _ResolvedParticipant) -> None:
            with semaphore:
                if cancel_event.is_set():
                    return
                result = self._run_turn(
                    session,
                    participant,
                    round_index,
                    prompt_factory(participant),
                    cancel_event,
                )
                with results_lock:
                    results[index] = result

        threads = [
            threading.Thread(
                target=run_one,
                args=(index, participant),
                name=f"discussion-turn-r{round_index}-{participant.spec.id}",
                daemon=True,
            )
            for index, participant in enumerate(participants)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return [result for result in results if result is not None]

    def _run_turn(
        self,
        session: DiscussionSession,
        participant: _ResolvedParticipant,
        round_index: int,
        prompt: str,
        cancel_event: threading.Event,
    ) -> _TurnResult:
        adapter = participant.adapter
        supports_token_stream = (
            adapter.supports_token_stream
            if adapter is not None
            else participant.spec.supports_token_stream
        )
        turn_id = self._begin_turn(
            session,
            participant.spec.id,
            round_index,
            supports_token_stream,
            cancel_event,
        )
        if turn_id is None:
            return _TurnResult(participant, None, False, "", "cancelled")
        if (
            adapter is None
            or not participant.detection.available
            or participant.detection.path is None
        ):
            error = participant.detection.error or f"{participant.spec.adapter_id} is unavailable"
            self._fail_turn(session, turn_id, error, _DeltaAccumulator(), cancel_event)
            return _TurnResult(participant, turn_id, False, "", error)

        accumulator = _DeltaAccumulator()
        terminal = threading.Event()
        outcome_lock = threading.Lock()
        success = False
        outcome_error: str | None = None

        def on_delta(text: str) -> None:
            if cancel_event.is_set():
                return
            combined = accumulator.add(text)
            if combined:
                self._append_delta(session, turn_id, combined, cancel_event)

        def on_done() -> None:
            nonlocal success
            completed = self._complete_turn(session, turn_id, accumulator, cancel_event)
            with outcome_lock:
                success = completed
            terminal.set()

        def on_error(message: str) -> None:
            nonlocal outcome_error
            self._fail_turn(session, turn_id, message, accumulator, cancel_event)
            with outcome_lock:
                outcome_error = message
            terminal.set()

        def on_cancelled() -> None:
            terminal.set()

        try:
            invocation = adapter.build_invocation(prompt, participant.spec.model)
            run_streaming(
                adapter,
                invocation,
                on_delta,
                on_done,
                on_error,
                on_cancelled,
                cancel_event,
            )
        except Exception as exc:
            on_error(str(exc))
        if not terminal.is_set() and not cancel_event.is_set():
            on_error("stream runner returned without a terminal callback")
        with outcome_lock:
            result_success = success
            result_error = outcome_error
        return _TurnResult(
            participant,
            turn_id,
            result_success,
            _turn_text(session, turn_id),
            result_error,
        )

    def _begin_turn(
        self,
        session: DiscussionSession,
        participant_id: str,
        round_index: int,
        supports_token_stream: bool,
        cancel_event: threading.Event,
    ) -> str | None:
        with self._event_order_lock:
            if cancel_event.is_set():
                return None
            turn = session.add_turn(
                participant_id,
                round_index,
                supports_token_stream=supports_token_stream,
            )
            event = session.start_turn(turn.id)
            self._enqueue_event_locked(event)
            return turn.id

    def _append_delta(
        self,
        session: DiscussionSession,
        turn_id: str,
        text: str,
        cancel_event: threading.Event,
    ) -> bool:
        with self._event_order_lock:
            if cancel_event.is_set():
                return False
            event = session.append_delta(turn_id, text)
            self._enqueue_event_locked(event)
            return True

    def _complete_turn(
        self,
        session: DiscussionSession,
        turn_id: str,
        accumulator: _DeltaAccumulator,
        cancel_event: threading.Event,
    ) -> bool:
        with self._event_order_lock:
            if cancel_event.is_set():
                return False
            remaining = accumulator.flush()
            if remaining:
                self._enqueue_event_locked(session.append_delta(turn_id, remaining))
            self._enqueue_event_locked(session.complete_turn(turn_id))
            return True

    def _fail_turn(
        self,
        session: DiscussionSession,
        turn_id: str,
        error: str,
        accumulator: _DeltaAccumulator,
        cancel_event: threading.Event,
    ) -> bool:
        with self._event_order_lock:
            if cancel_event.is_set():
                return False
            remaining = accumulator.flush()
            if remaining:
                self._enqueue_event_locked(session.append_delta(turn_id, remaining))
            self._enqueue_event_locked(session.fail_turn(turn_id, error))
            return True

    def _transition(
        self,
        session: DiscussionSession,
        cancel_event: threading.Event,
        status: SessionStatus,
        *,
        error: str | None = None,
    ) -> bool:
        with self._event_order_lock:
            if cancel_event.is_set():
                return False
            event = session.transition(status, error=error)
            if event is not None:
                self._enqueue_event_locked(event)
            return True

    def _enqueue_event_locked(self, event: DiscussionEvent) -> None:
        listener: Callable[[], None] | None = None
        with self._event_lock:
            if not self._callbacks_enabled:
                return
            was_empty = not self._events
            self._events.append(event)
            if was_empty:
                listener = self._event_listener
        if listener is not None:
            try:
                listener()
            except Exception:
                return


def _default_adapter_factory(spec: ParticipantSpec) -> CLIAdapter:
    if spec.source == "argv" or spec.source == "login_shell":
        return _CustomLineAdapter(spec)
    if spec.adapter_id == "claude":
        return ClaudeAdapter(
            cwd=spec.cwd,
            env_overrides=spec.env_overrides,
            timeout_seconds=spec.timeout_seconds,
        )
    if spec.adapter_id == "codex":
        return CodexAdapter(
            cwd=spec.cwd,
            env_overrides=spec.env_overrides,
            timeout_seconds=spec.timeout_seconds,
        )
    if spec.adapter_id == "gemini":
        return GeminiAdapter(
            cwd=spec.cwd,
            env_overrides=spec.env_overrides,
            timeout_seconds=spec.timeout_seconds,
        )
    raise ValueError(f"unknown built-in adapter: {spec.adapter_id}")


def _select_moderator(
    survivors: Sequence[_TurnResult],
    moderator_id: str | None,
) -> _TurnResult | None:
    if moderator_id is not None:
        for survivor in survivors:
            if survivor.participant.spec.id == moderator_id:
                return survivor
    return survivors[0] if survivors else None


def _turn_text(session: DiscussionSession, turn_id: str) -> str:
    snapshot = session.snapshot()
    for turn in snapshot["turns"]:
        if turn["id"] == turn_id:
            return str(turn["text"])
    return ""


def _build_transcript(session: DiscussionSession) -> str:
    snapshot = session.snapshot()
    labels = {
        str(participant["id"]): str(participant["label"])
        for participant in snapshot["participants"]
    }
    sections: list[str] = []
    for turn in snapshot["turns"]:
        participant_id = str(turn["participant_id"])
        error = turn["error"]
        body = str(turn["text"])
        if error:
            body = f"{body}\n[失敗：{error}]" if body else f"[失敗：{error}]"
        sections.append(
            f"<<<TURN participant={labels.get(participant_id, participant_id)!r} "
            f"round={turn['round_index']} status={turn['status']}>>>\n"
            f"{body}\n<<<TURN_END>>>"
        )
    return "\n\n".join(sections)
