from __future__ import annotations

from ui.tables import _fmt_cost


def test_fmt_cost_returns_placeholder_for_none() -> None:
    assert _fmt_cost(None) == "--"


def test_fmt_cost_preserves_zero_behavior() -> None:
    assert _fmt_cost(0.0) == "$0"


def test_fmt_cost_preserves_positive_decimal_behavior() -> None:
    assert _fmt_cost(1.234) == "$1.23"
