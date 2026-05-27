from __future__ import annotations

import sys
from datetime import UTC, datetime
from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest

from adapters.types import AgentInfo, RateLimits, UsageEntry

usage_cli: Any = import_module("usage_cli")


def _entry() -> UsageEntry:
    return UsageEntry(
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        session_id="session-1",
        message_id="message-1",
        request_id="request-1",
        model="gpt-test",
        input_tokens=10,
        output_tokens=5,
        cache_creation_tokens=2,
        cache_read_tokens=3,
        cost_usd=0.01,
        project="project",
        agent_id="codex",
    )


def test_parse_sort_args_extracts_major_flags() -> None:
    remaining, sort_key, descending = usage_cli._parse_sort_args(
        ["30", "--sort", "cost", "--asc"]
    )

    assert remaining == ["30"]
    assert sort_key == "cost"
    assert descending is False


def test_main_dashboard_uses_mocked_loaders_without_touching_agent_dirs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = AgentInfo("codex", "Codex", "~/.codex", True)
    rendered: dict[str, Any] = {}

    monkeypatch.setattr(sys, "argv", ["usage", "dashboard"])
    monkeypatch.setattr(usage_cli, "detect_agents", lambda: [agent])
    monkeypatch.setattr(usage_cli, "is_setup", lambda: True)
    monkeypatch.setattr(usage_cli, "_load_entries", lambda agent_id: [_entry()])
    monkeypatch.setattr(
        usage_cli,
        "RATE_LIMIT_LOADERS",
        {"codex": lambda: RateLimits(five_hour_pct=12, seven_day_pct=34)},
    )
    monkeypatch.setattr(usage_cli, "render_dashboard", lambda **kwargs: rendered.update(kwargs))

    usage_cli.main()

    assert rendered["agents"] == ["Codex"]
    assert len(rendered["daily_stats"]) == 1
    assert rendered["rate_limits"] == RateLimits(five_hour_pct=12, seven_day_pct=34)


def test_main_daily_sort_flag_controls_render_order(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = AgentInfo("codex", "Codex", "~/.codex", True)
    high = _entry()
    low = _entry()
    high.input_tokens = 100
    high.timestamp = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    low.input_tokens = 1
    low.timestamp = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    rendered: dict[str, Any] = {}

    monkeypatch.setattr(sys, "argv", ["usage", "daily", "--sort", "tokens", "--asc"])
    monkeypatch.setattr(usage_cli, "detect_agents", lambda: [agent])
    monkeypatch.setattr(usage_cli, "is_setup", lambda: True)
    monkeypatch.setattr(usage_cli, "_load_entries", lambda agent_id: [high, low])
    monkeypatch.setattr(
        usage_cli,
        "render_daily",
        lambda stats, agents: rendered.update(stats=stats),
    )

    usage_cli.main()

    assert [stat.total_tokens for stat in rendered["stats"]] == [11, 110]


@pytest.mark.parametrize(
    ("argv", "expected_period"),
    [
        (["usage", "report"], "last30"),
        (["usage", "report", "--last30"], "last30"),
        (["usage", "report", "--all"], "all"),
    ],
)
def test_main_report_parses_period(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    expected_period: str,
) -> None:
    from analyzer import reporter
    from ui import html_report

    agent = AgentInfo("codex", "Codex", "~/.codex", True)
    calls: dict[str, Any] = {}

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(usage_cli, "detect_agents", lambda: [agent])
    monkeypatch.setattr(usage_cli, "is_setup", lambda: True)
    monkeypatch.setattr(
        reporter,
        "build_report_data",
        lambda agents, period: calls.update(agents=agents, period=period) or {},
    )
    monkeypatch.setattr(html_report, "save_and_open", lambda data, out_path=None: "report.html")

    usage_cli.main()

    assert calls == {"agents": [agent], "period": expected_period}


def test_main_report_help_does_not_build_report(monkeypatch: pytest.MonkeyPatch) -> None:
    from analyzer import reporter

    printed: list[str] = []

    monkeypatch.setattr(sys, "argv", ["usage", "report", "--help"])
    monkeypatch.setattr(
        usage_cli,
        "detect_agents",
        lambda: pytest.fail("report help should not detect agents"),
    )
    monkeypatch.setattr(usage_cli, "is_setup", lambda: True)
    monkeypatch.setattr(usage_cli.console, "print", lambda value: printed.append(str(value)))
    monkeypatch.setattr(
        reporter,
        "build_report_data",
        lambda agents, period: pytest.fail("report help should not build a report"),
    )

    usage_cli.main()

    assert any("Usage: usage report" in line for line in printed)


def test_main_report_rejects_unknown_option(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = AgentInfo("codex", "Codex", "~/.codex", True)
    printed: list[str] = []

    monkeypatch.setattr(sys, "argv", ["usage", "report", "--bogus"])
    monkeypatch.setattr(usage_cli, "detect_agents", lambda: [agent])
    monkeypatch.setattr(usage_cli, "is_setup", lambda: True)
    monkeypatch.setattr(usage_cli.console, "print", lambda value: printed.append(str(value)))

    with pytest.raises(SystemExit) as exc_info:
        usage_cli.main()

    assert exc_info.value.code == 1
    assert any("unknown report option" in line for line in printed)


def test_main_exits_when_no_agents_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["usage", "dashboard"])
    monkeypatch.setattr(usage_cli, "detect_agents", lambda: [])

    with pytest.raises(SystemExit) as exc_info:
        usage_cli.main()

    assert exc_info.value.code == 1


def test_parse_report_args_defaults_to_last30() -> None:
    assert usage_cli._parse_report_args([]) == ("last30", None, False)


@pytest.mark.parametrize(
    ("flag", "expected_period"),
    [
        ("--today", "today"),
        ("--week", "week"),
        ("--month", "month"),
        ("--all", "all"),
        ("--last30", "last30"),
    ],
)
def test_parse_report_args_sets_period(flag: str, expected_period: str) -> None:
    period, out_path, show_help = usage_cli._parse_report_args([flag])

    assert period == expected_period
    assert out_path is None
    assert show_help is False


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_parse_report_args_detects_help(flag: str) -> None:
    assert usage_cli._parse_report_args([flag]) == ("last30", None, True)


@pytest.mark.parametrize(
    ("args", "expected_path"),
    [
        (["--out=report.html"], "report.html"),
        (["--out", "report.html"], "report.html"),
    ],
)
def test_parse_report_args_sets_out_path(args: list[str], expected_path: str) -> None:
    assert usage_cli._parse_report_args(args) == ("last30", expected_path, False)


@pytest.mark.parametrize(
    "args",
    [
        ["--out"],
        ["--out", "--today"],
    ],
)
def test_parse_report_args_rejects_missing_out_path(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    printed: list[str] = []
    monkeypatch.setattr(usage_cli.console, "print", lambda value: printed.append(str(value)))

    with pytest.raises(SystemExit) as exc_info:
        usage_cli._parse_report_args(args)

    assert exc_info.value.code == 1
    assert any("--out requires a path" in line for line in printed)


@pytest.mark.parametrize(
    ("args", "expected_message"),
    [
        (["--bogus"], "unknown report option"),
        (["random"], "unexpected report argument"),
    ],
)
def test_parse_report_args_rejects_invalid_args(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    expected_message: str,
) -> None:
    printed: list[str] = []
    monkeypatch.setattr(usage_cli.console, "print", lambda value: printed.append(str(value)))

    with pytest.raises(SystemExit) as exc_info:
        usage_cli._parse_report_args(args)

    assert exc_info.value.code == 1
    assert any(expected_message in line for line in printed)


def test_apply_sort_uses_default_when_sort_key_is_none() -> None:
    stats = [
        SimpleNamespace(start_time=3, total_tokens=0, cost_usd=0.0, message_count=0),
        SimpleNamespace(start_time=1, total_tokens=0, cost_usd=0.0, message_count=0),
        SimpleNamespace(start_time=2, total_tokens=0, cost_usd=0.0, message_count=0),
    ]

    usage_cli._apply_sort(stats, None, True, "start_time", False)

    assert [stat.start_time for stat in stats] == [1, 2, 3]


@pytest.mark.parametrize(
    ("descending", "expected_costs"),
    [
        (True, [3.0, 2.0, 1.0]),
        (False, [1.0, 2.0, 3.0]),
    ],
)
def test_apply_sort_uses_known_sort_key(descending: bool, expected_costs: list[float]) -> None:
    assert "cost" in usage_cli.SORT_KEYS
    stats = [
        SimpleNamespace(start_time=1, total_tokens=0, cost_usd=2.0, message_count=0),
        SimpleNamespace(start_time=2, total_tokens=0, cost_usd=1.0, message_count=0),
        SimpleNamespace(start_time=3, total_tokens=0, cost_usd=3.0, message_count=0),
    ]

    usage_cli._apply_sort(stats, "cost", descending, "start_time", False)

    assert [stat.cost_usd for stat in stats] == expected_costs


def test_apply_sort_unknown_key_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed: list[str] = []
    stats = [
        SimpleNamespace(start_time=3, total_tokens=0, cost_usd=1.0, message_count=0),
        SimpleNamespace(start_time=1, total_tokens=0, cost_usd=3.0, message_count=0),
        SimpleNamespace(start_time=2, total_tokens=0, cost_usd=2.0, message_count=0),
    ]
    monkeypatch.setattr(usage_cli.console, "print", lambda value: printed.append(str(value)))

    usage_cli._apply_sort(stats, "unknown_key", True, "start_time", False)

    assert [stat.start_time for stat in stats] == [1, 2, 3]
    assert any("unknown_key" in line for line in printed)


@pytest.mark.parametrize(
    ("env_name", "expected_id"),
    [
        ("CODEX_THREAD_ID", "codex"),
        ("CODEX_SANDBOX", "codex"),
        ("CLAUDE_CONFIG_DIR", "claude-code"),
        ("CLAUDECODE", "claude-code"),
    ],
)
def test_initial_agent_index_uses_environment_preference(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    expected_id: str,
) -> None:
    for name in ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CLAUDE_CONFIG_DIR", "CLAUDECODE"):
        monkeypatch.delenv(name, raising=False)
    agents = [SimpleNamespace(id="other"), SimpleNamespace(id=expected_id)]

    monkeypatch.setenv(env_name, "1")

    assert usage_cli._initial_agent_index(agents) == 1


def test_initial_agent_index_defaults_to_first_without_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CLAUDE_CONFIG_DIR", "CLAUDECODE"):
        monkeypatch.delenv(name, raising=False)

    assert usage_cli._initial_agent_index([SimpleNamespace(id="codex")]) == 0


def test_initial_agent_index_defaults_to_first_when_preferred_agent_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CLAUDE_CONFIG_DIR", "CLAUDECODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "1")

    assert usage_cli._initial_agent_index([SimpleNamespace(id="claude-code")]) == 0


def test_fit_screen_returns_empty_text() -> None:
    assert usage_cli._fit_screen("", 10, 0) == ("", 0)


def test_fit_screen_returns_full_text_when_it_fits() -> None:
    assert usage_cli._fit_screen("header\nbody", 5, 0) == ("header\nbody", 0)


def test_fit_screen_limits_body_to_scroll_window() -> None:
    screen, max_scroll = usage_cli._fit_screen("h\nb1\nb2\nb3\nb4", 4, 1)

    assert screen == "h\nb2\nb3"
    assert max_scroll == 2


def test_fit_screen_clamps_scroll_offset() -> None:
    screen, max_scroll = usage_cli._fit_screen("h\nb1\nb2\nb3\nb4", 4, 99)

    assert screen == "h\nb3\nb4"
    assert max_scroll == 2


def test_dashboard_sort_cycle_shape_and_order() -> None:
    sort_cycle = usage_cli._dashboard_sort_cycle()

    assert len(sort_cycle) == 4
    assert all(len(item) == 3 for item in sort_cycle)
    assert [item[0] for item in sort_cycle] == ["time", "tokens", "cost", "messages"]
    assert [item[1] for item in sort_cycle] == [
        "start_time",
        "total_tokens",
        "cost_usd",
        "message_count",
    ]
