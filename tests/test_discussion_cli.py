# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>
#
# Part of "usage". Free software licensed under the GNU Affero General Public
# License v3.0 only; see the LICENSE file for full terms and the warranty disclaimer.

from __future__ import annotations

import io
import json
import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

import discussion_cli
from discussion_cli import (
    ClaudeAdapter,
    CLIAdapter,
    CodexAdapter,
    GeminiAdapter,
    Invocation,
    NeutralWorkingDirectoryError,
)


class FakeProcess:
    def __init__(
        self,
        stdout: Iterable[str],
        stderr: Iterable[str],
        *,
        returncode: int | None = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.pid = 4321

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired(("fake",), timeout or 0.0)
        return self.returncode


class RecordingStream:
    def __init__(self, lines: list[str], thread_names: list[str]) -> None:
        self._lines = lines
        self._thread_names = thread_names

    def __iter__(self) -> Iterator[str]:
        self._thread_names.append(threading.current_thread().name)
        yield from self._lines


def _invocation(*, timeout: float = 1.0, env: dict[str, str] | None = None) -> Invocation:
    return Invocation(
        argv=("/fake/claude",),
        cwd=None,
        env_overrides={} if env is None else env,
        timeout_seconds=timeout,
    )


def _claude_delta(text: str) -> str:
    return json.dumps(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": text},
            },
        }
    )


def _claude_done() -> str:
    return '{"type":"result","subtype":"success"}'


@pytest.fixture(autouse=True)
def _isolated_neutral_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        discussion_cli,
        "NEUTRAL_DISCUSSION_CWD",
        tmp_path / "neutral-discussion-cwd",
    )


def _install_fake_popen(
    monkeypatch: pytest.MonkeyPatch,
    process: FakeProcess,
    captured: dict[str, Any] | None = None,
) -> None:
    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        if captured is not None:
            captured["args"] = args
            captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)


def _run(
    adapter: CLIAdapter,
    invocation: Invocation,
    *,
    cancel_event: threading.Event | None = None,
) -> tuple[list[str], list[str], int, int]:
    deltas: list[str] = []
    errors: list[str] = []
    done_count = 0
    cancelled_count = 0

    def on_done() -> None:
        nonlocal done_count
        done_count += 1

    def on_cancelled() -> None:
        nonlocal cancelled_count
        cancelled_count += 1

    discussion_cli.run_streaming(
        adapter,
        invocation,
        deltas.append,
        on_done,
        errors.append,
        on_cancelled,
        cancel_event or threading.Event(),
    )
    return deltas, errors, done_count, cancelled_count


def test_detection_order_user_configured_then_which_then_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "configured-claude"
    configured.write_text("#!/bin/sh\n")
    configured.chmod(0o755)
    which_path = tmp_path / "which-claude"
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    candidate = candidate_dir / "claude"
    candidate.write_text("#!/bin/sh\n")
    candidate.chmod(0o755)

    monkeypatch.setattr(shutil, "which", lambda name: str(which_path))
    monkeypatch.setattr(discussion_cli, "CANDIDATE_DIRECTORIES", (candidate_dir,))
    assert ClaudeAdapter(str(configured)).detect().source == "user_configured"

    adapter = ClaudeAdapter()
    result = adapter.detect()
    assert result.source == "which"
    assert result.path == str(which_path)

    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = adapter.detect()
    assert result.source == "candidate_dir"
    assert result.path == str(candidate)

    candidate.chmod(0o644)
    assert adapter.detect().source == "not_found"


def test_invalid_configured_path_does_not_silently_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/echo")
    result = ClaudeAdapter("relative/claude").detect()

    assert result.available is False
    assert result.source == "user_configured"
    assert result.error == "configured CLI path must be absolute"


@pytest.mark.parametrize(
    ("adapter", "expected"),
    [
        (
            ClaudeAdapter("/bin/echo"),
            (
                "/bin/echo",
                "-p",
                "--safe-mode",
                "--setting-sources",
                "project",
                "--output-format",
                "stream-json",
                "--include-partial-messages",
                "--verbose",
                "問題",
            ),
        ),
        (
            CodexAdapter("/bin/echo"),
            (
                "/bin/echo",
                "exec",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--json",
                "問題",
            ),
        ),
        (
            GeminiAdapter("/bin/echo"),
            ("/bin/echo", "-o", "stream-json", "-p", "問題"),
        ),
    ],
)
def test_builtin_invocation_argv_is_exact(
    adapter: CLIAdapter,
    expected: tuple[str, ...],
) -> None:
    invocation = adapter.build_invocation("問題", None)

    assert invocation.argv == expected
    assert invocation.cwd == str(discussion_cli.NEUTRAL_DISCUSSION_CWD)


def test_codex_invocation_never_bypasses_approvals_or_sandbox() -> None:
    invocation = CodexAdapter("/bin/echo").build_invocation("問題", None)

    assert "--skip-git-repo-check" in invocation.argv
    assert "--ignore-user-config" in invocation.argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in invocation.argv


def test_claude_invocation_uses_safe_mode_without_bare() -> None:
    invocation = ClaudeAdapter("/bin/echo").build_invocation("問題", None)

    assert "--safe-mode" in invocation.argv
    assert "--setting-sources" in invocation.argv
    assert invocation.argv[invocation.argv.index("--setting-sources") + 1] == "project"
    assert "--bare" not in invocation.argv


def test_neutral_working_directory_is_created_without_configuration() -> None:
    path = Path(discussion_cli.resolve_neutral_working_directory())

    assert path.is_dir()
    assert list(path.iterdir()) == []
    assert not any((path / name).exists() for name in discussion_cli.NEUTRAL_CONFIG_NAMES)


def test_neutral_working_directory_rejects_config_without_deleting_it() -> None:
    path = discussion_cli.NEUTRAL_DISCUSSION_CWD
    path.mkdir(parents=True)
    config = path / "CLAUDE.md"
    config.write_text("personal instructions")

    with pytest.raises(NeutralWorkingDirectoryError, match="CLAUDE.md"):
        discussion_cli.resolve_neutral_working_directory()

    assert config.read_text() == "personal instructions"


def test_neutral_working_directory_wraps_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError("permission denied")

    monkeypatch.setattr(Path, "mkdir", deny_mkdir)

    with pytest.raises(NeutralWorkingDirectoryError, match="permission denied"):
        discussion_cli.resolve_neutral_working_directory()


def test_explicit_cwd_bypasses_default_neutral_directory(tmp_path: Path) -> None:
    explicit = tmp_path / "project"
    invocation = ClaudeAdapter("/bin/echo", cwd=str(explicit)).build_invocation("問題", None)

    assert invocation.cwd == str(explicit)


@pytest.mark.parametrize(
    ("adapter", "required"),
    [
        (
            ClaudeAdapter,
            (
                "--safe-mode",
                "--setting-sources",
                "project",
                "--tools",
                "Read,Grep,Glob",
            ),
        ),
        (
            CodexAdapter,
            (
                "--skip-git-repo-check",
                "--ignore-user-config",
                "-s",
                "read-only",
            ),
        ),
    ],
)
def test_builtin_project_invocation_is_read_only(
    tmp_path: Path,
    adapter: type[ClaudeAdapter] | type[CodexAdapter],
    required: tuple[str, ...],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    invocation = adapter(
        "/bin/echo",
        cwd=str(project),
        read_only=True,
    ).build_invocation("問題", None)

    assert invocation.cwd == str(project.resolve())
    assert all(item in invocation.argv for item in required)


def test_gemini_project_invocation_only_changes_cwd(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    invocation = GeminiAdapter(
        "/bin/echo",
        cwd=str(project),
        read_only=True,
    ).build_invocation("問題", None)

    assert invocation.cwd == str(project.resolve())
    assert invocation.argv == ("/bin/echo", "-o", "stream-json", "-p", "問題")


@pytest.mark.parametrize(
    "forbidden",
    [
        "--dangerously-bypass-approvals-and-sandbox",
        "workspace-write",
        "danger-full-access",
        "bypassPermissions",
        "acceptEdits",
        "dontAsk",
    ],
)
def test_project_invocations_never_enable_writes(
    tmp_path: Path,
    forbidden: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    invocations = [
        adapter("/bin/echo", cwd=str(project), read_only=True).build_invocation(
            "問題", None
        )
        for adapter in (ClaudeAdapter, CodexAdapter, GeminiAdapter)
    ]

    assert all(forbidden not in invocation.argv for invocation in invocations)


@pytest.mark.parametrize("kind", ["blank", "missing", "file"])
def test_project_invocation_rejects_invalid_working_directory(
    tmp_path: Path,
    kind: str,
) -> None:
    path = tmp_path / kind
    if kind == "file":
        path.write_text("not a directory")
    value = "" if kind == "blank" else str(path)

    with pytest.raises(ValueError, match="working directory"):
        ClaudeAdapter(
            "/bin/echo",
            cwd=value,
            read_only=True,
        ).build_invocation("問題", None)


def test_protocol_exposes_coordinator_metadata() -> None:
    adapter: CLIAdapter = ClaudeAdapter()

    assert adapter.adapter_id == "claude"
    assert adapter.supports_token_stream is True


def test_adapters_parse_documented_events_and_finish_markers() -> None:
    claude = ClaudeAdapter()
    codex = CodexAdapter()
    gemini = GeminiAdapter()

    assert claude.parse_stdout_line(_claude_delta("逐字")) == ("逐字", False)
    assert claude.parse_stdout_line('{"type":"result","subtype":"success"}') == (None, True)
    assert codex.parse_stdout_line(
        '{"type":"item.completed","item":{"type":"agent_message","text":"整段"}}'
    ) == ("整段", False)
    assert codex.parse_stdout_line('{"type":"turn.completed","usage":{}}') == (None, True)
    assert gemini.parse_stdout_line(
        '{"type":"message","role":"assistant","content":"片段","delta":true}'
    ) == ("片段", False)
    assert gemini.parse_stdout_line('{"type":"result","status":"success"}') == (None, True)


@pytest.mark.parametrize("adapter", [ClaudeAdapter(), CodexAdapter(), GeminiAdapter()])
def test_invalid_json_is_skipped_and_counted(adapter: Any) -> None:
    before = adapter.parse_error_count

    assert adapter.parse_stdout_line("not-json") == (None, False)
    assert adapter.parse_error_count == before + 1


def test_normal_streaming_uses_parsed_deltas_and_separate_reader_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader_threads: list[str] = []
    process = FakeProcess(
        RecordingStream(
            [_claude_delta("甲") + "\n", _claude_delta("乙") + "\n", _claude_done()],
            reader_threads,
        ),
        RecordingStream(["warning\n"], reader_threads),
    )
    captured: dict[str, Any] = {}
    _install_fake_popen(monkeypatch, process, captured)

    deltas, errors, done_count, cancelled_count = _run(ClaudeAdapter(), _invocation())

    assert deltas == ["甲", "乙"]
    assert errors == []
    assert done_count == 1
    assert cancelled_count == 0
    assert set(reader_threads) == {"discussion-cli-stdout", "discussion-cli-stderr"}
    kwargs = captured["kwargs"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert kwargs["shell"] is False
    assert kwargs["bufsize"] == 1

def test_final_line_without_newline_is_processed_at_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(io.StringIO(_claude_delta("最後一段")), io.StringIO())
    _install_fake_popen(monkeypatch, process)

    deltas, errors, done_count, cancelled_count = _run(ClaudeAdapter(), _invocation())

    assert deltas == ["最後一段"]
    assert errors == ["CLI exited without a completion event; output may be incomplete"]
    assert done_count == 0
    assert cancelled_count == 0


def test_large_stderr_does_not_block_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = "".join(f"noise-{index}\n" for index in range(10_000))
    process = FakeProcess(
        io.StringIO(_claude_delta("答案") + "\n" + _claude_done()),
        io.StringIO(stderr),
    )
    _install_fake_popen(monkeypatch, process)

    deltas, errors, done_count, cancelled_count = _run(ClaudeAdapter(), _invocation())

    assert deltas == ["答案"]
    assert errors == []
    assert done_count == 1
    assert cancelled_count == 0


def test_timeout_terminates_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(io.StringIO(), io.StringIO(), returncode=None)
    _install_fake_popen(monkeypatch, process)
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(os, "getpgid", lambda pid: 9001)

    def fake_killpg(process_group: int, sent_signal: signal.Signals) -> None:
        signals.append((process_group, sent_signal))
        process.returncode = -int(sent_signal)

    monkeypatch.setattr(os, "killpg", fake_killpg)

    _, errors, done_count, cancelled_count = _run(ClaudeAdapter(), _invocation(timeout=0))

    assert signals == [(9001, signal.SIGTERM)]
    assert errors == ["CLI invocation timed out after 0 seconds"]
    assert done_count == 0
    assert cancelled_count == 0


def test_sigterm_escalates_to_sigkill(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(io.StringIO(), io.StringIO(), returncode=None)
    _install_fake_popen(monkeypatch, process)
    signals: list[signal.Signals] = []
    monkeypatch.setattr(os, "getpgid", lambda pid: 9002)

    def fake_killpg(process_group: int, sent_signal: signal.Signals) -> None:
        signals.append(sent_signal)
        if sent_signal is signal.SIGKILL:
            process.returncode = -int(sent_signal)

    monkeypatch.setattr(os, "killpg", fake_killpg)

    _, errors, done_count, cancelled_count = _run(ClaudeAdapter(), _invocation(timeout=0))

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert errors == ["CLI invocation timed out after 0 seconds"]
    assert done_count == 0
    assert cancelled_count == 0


def test_cancellation_terminates_process_group_and_commits_cancelled_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(io.StringIO(), io.StringIO(), returncode=None)
    _install_fake_popen(monkeypatch, process)
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(os, "getpgid", lambda pid: 9003)

    def fake_killpg(process_group: int, sent_signal: signal.Signals) -> None:
        signals.append((process_group, sent_signal))
        process.returncode = -int(sent_signal)

    monkeypatch.setattr(os, "killpg", fake_killpg)
    cancel_event = threading.Event()
    cancel_event.set()

    _, errors, done_count, cancelled_count = _run(
        ClaudeAdapter(),
        _invocation(),
        cancel_event=cancel_event,
    )

    assert signals == [(9003, signal.SIGTERM)]
    assert errors == []
    assert done_count == 0
    assert cancelled_count == 1


def test_cancel_and_normal_completion_race_commits_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(io.StringIO(_claude_done()), io.StringIO(), returncode=0)
    _install_fake_popen(monkeypatch, process)
    cancel_event = threading.Event()
    cancel_event.set()

    _, errors, done_count, cancelled_count = _run(
        ClaudeAdapter(),
        _invocation(),
        cancel_event=cancel_event,
    )

    assert errors == []
    assert done_count == 1
    assert cancelled_count == 0


def test_nonzero_exit_reports_stderr_tail_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(
        io.StringIO(),
        io.StringIO("Authentication failed\nIneligibleTierError\n"),
        returncode=1,
    )
    _install_fake_popen(monkeypatch, process)

    _, errors, done_count, cancelled_count = _run(GeminiAdapter(), _invocation())

    assert errors == ["Authentication failed\nIneligibleTierError"]
    assert done_count == 0
    assert cancelled_count == 0


def test_nonzero_exit_uses_stdout_tail_when_stderr_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(
        io.StringIO("Not logged in · Please run /login\n"),
        io.StringIO(),
        returncode=1,
    )
    _install_fake_popen(monkeypatch, process)

    _, errors, done_count, cancelled_count = _run(ClaudeAdapter(), _invocation())

    assert errors == ["Not logged in · Please run /login"]
    assert done_count == 0
    assert cancelled_count == 0


def test_stdout_diagnostic_tail_has_fixed_line_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = "".join(f"stdout-{index}\n" for index in range(60))
    process = FakeProcess(io.StringIO(stdout), io.StringIO(), returncode=1)
    _install_fake_popen(monkeypatch, process)

    _, errors, _, _ = _run(ClaudeAdapter(), _invocation())

    lines = errors[0].splitlines()
    assert len(lines) == discussion_cli.STDERR_TAIL_LINES
    assert lines[0] == "stdout-10"
    assert lines[-1] == "stdout-59"


def test_sensitive_environment_values_are_redacted_from_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(
        io.StringIO(),
        io.StringIO("request failed for secret-value\n"),
        returncode=1,
    )
    _install_fake_popen(monkeypatch, process)

    _, errors, _, _ = _run(
        ClaudeAdapter(),
        _invocation(env={"SERVICE_TOKEN": "secret-value"}),
    )

    assert errors == ["request failed for [REDACTED]"]


def test_stream_output_limit_appends_visible_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discussion_cli, "MAX_STREAM_OUTPUT_CHARS", 5)
    process = FakeProcess(
        io.StringIO(
            _claude_delta("123456789")
            + "\n"
            + _claude_delta("ignored")
            + "\n"
            + _claude_done()
        ),
        io.StringIO(),
    )
    _install_fake_popen(monkeypatch, process)

    deltas, errors, done_count, cancelled_count = _run(ClaudeAdapter(), _invocation())

    assert "".join(deltas) == "12345" + discussion_cli.TRUNCATION_MARKER
    assert errors == []
    assert done_count == 1
    assert cancelled_count == 0


def test_custom_command_modes_keep_prompt_as_separate_argv() -> None:
    prompt = "$(touch /tmp/must-not-run); 'quoted'"
    argv_mode = discussion_cli.build_argv_invocation(
        "/custom/tool",
        ("--json",),
        ("--after",),
        prompt,
    )
    shell_mode = discussion_cli.build_login_shell_invocation(
        'tool --prompt "$1"',
        prompt,
        opt_in=True,
    )

    assert argv_mode.argv == ("/custom/tool", "--json", prompt, "--after")
    assert shell_mode.argv == (
        "/bin/zsh",
        "-lic",
        'tool --prompt "$1"',
        "usage-discussion",
        prompt,
    )
    with pytest.raises(ValueError, match="explicit opt-in"):
        discussion_cli.build_login_shell_invocation("tool", prompt, opt_in=False)
