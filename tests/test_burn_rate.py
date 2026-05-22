from __future__ import annotations

import pytest

from burn_rate import BurnRateTracker


def test_forecast_none_for_empty_buffer() -> None:
    tracker = BurnRateTracker()

    assert tracker.forecast_seconds() is None


def test_forecast_none_for_single_sample() -> None:
    tracker = BurnRateTracker()
    tracker.record(100.0, 20.0)

    assert tracker.forecast_seconds() is None


def test_forecast_uses_recent_samples() -> None:
    tracker = BurnRateTracker()
    tracker.record(0.0, 10.0)
    tracker.record(300.0, 40.0)

    assert tracker.forecast_seconds() == pytest.approx(600.0)


def test_record_detects_reset_and_clears_old_samples() -> None:
    tracker = BurnRateTracker()
    tracker.record(0.0, 60.0)
    tracker.record(60.0, 68.0)
    tracker.record(120.0, 10.0)
    tracker.record(180.0, 20.0)

    assert tracker.forecast_seconds() == pytest.approx(480.0)


def test_forecast_none_for_negative_slope() -> None:
    tracker = BurnRateTracker()
    tracker.record(0.0, 60.0)
    tracker.record(300.0, 40.0)

    assert tracker.forecast_seconds() is None


def test_record_prunes_samples_older_than_rolling_window() -> None:
    tracker = BurnRateTracker()
    tracker.record(0.0, 10.0)
    tracker.record(400.0, 60.0)
    tracker.record(901.0, 80.0)

    assert tracker.forecast_seconds() == pytest.approx(501.0)
