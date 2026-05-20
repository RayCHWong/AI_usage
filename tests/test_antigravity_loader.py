from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import antigravity_loader


def _urlopen_context(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    context = MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = None
    return context


def test_load_antigravity_returns_empty_snapshot_when_creds_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(antigravity_loader, "CREDS_PATH", tmp_path / "missing-creds.json")

    snapshot = antigravity_loader.load_antigravity()

    assert snapshot.used_percent is None
    assert snapshot.remaining_fraction is None
    assert snapshot.model_id is None
    assert snapshot.resets_at is None
    assert snapshot.weekly_used_percent is None
    assert snapshot.weekly_resets_at is None
    assert snapshot.polled_at is not None


def test_load_antigravity_reads_quota_bucket_for_valid_access_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    creds_path = tmp_path / "oauth_creds.json"
    creds_path.write_text(
        json.dumps(
            {
                "access_token": "access-token",
                "expiry_date": 9_999_999_999_999,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(antigravity_loader, "CREDS_PATH", creds_path)

    with patch("antigravity_loader.urllib.request.urlopen") as urlopen_mock:
        urlopen_mock.return_value = _urlopen_context(
            {
                "buckets": [
                    {
                        "remainingFraction": 0.3,
                        "modelId": "gemini-2.5-pro",
                        "resetTime": "2026-05-21T00:00:00Z",
                    }
                ]
            }
        )

        snapshot = antigravity_loader.load_antigravity()

    assert snapshot.used_percent == 70
    assert snapshot.remaining_fraction == 0.3
    assert snapshot.model_id == "gemini-2.5-pro"
    assert snapshot.resets_at is not None
    assert snapshot.weekly_used_percent is None
    assert snapshot.weekly_resets_at is None


def test_load_antigravity_splits_session_and_weekly_buckets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = 1_800_000_000.0
    creds_path = tmp_path / "oauth_creds.json"
    creds_path.write_text(
        json.dumps(
            {
                "access_token": "access-token",
                "expiry_date": 9_999_999_999_999,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(antigravity_loader, "CREDS_PATH", creds_path)

    session_reset = datetime.fromtimestamp(now + 12 * 3600, UTC).isoformat().replace(
        "+00:00", "Z"
    )
    weekly_reset = datetime.fromtimestamp(now + 6 * 86400, UTC).isoformat().replace(
        "+00:00", "Z"
    )
    with (
        patch("antigravity_loader.time.time", return_value=now),
        patch("antigravity_loader.urllib.request.urlopen") as urlopen_mock,
    ):
        urlopen_mock.return_value = _urlopen_context(
            {
                "buckets": [
                    {
                        "remainingFraction": 0.72,
                        "modelId": "session-model",
                        "resetTime": session_reset,
                    },
                    {
                        "remainingFraction": 0.59,
                        "modelId": "weekly-model",
                        "resetTime": weekly_reset,
                    },
                    {
                        "remainingFraction": 0.01,
                        "modelId": "bad-reset",
                        "resetTime": "not-a-date",
                    },
                ]
            }
        )

        snapshot = antigravity_loader.load_antigravity()

    assert snapshot.used_percent == 28
    assert snapshot.remaining_fraction == 0.72
    assert snapshot.model_id == "session-model"
    assert snapshot.resets_at == now + 12 * 3600
    assert snapshot.weekly_used_percent == 41
    assert snapshot.weekly_resets_at == now + 6 * 86400


def test_load_antigravity_returns_none_used_percent_when_api_has_no_buckets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    creds_path = tmp_path / "oauth_creds.json"
    creds_path.write_text(
        json.dumps(
            {
                "access_token": "access-token",
                "expiry_date": 9_999_999_999_999,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(antigravity_loader, "CREDS_PATH", creds_path)

    with patch("antigravity_loader.urllib.request.urlopen") as urlopen_mock:
        urlopen_mock.return_value = _urlopen_context({"buckets": []})

        snapshot = antigravity_loader.load_antigravity()

    assert snapshot.used_percent is None
    assert snapshot.weekly_used_percent is None


def test_load_antigravity_returns_empty_snapshot_when_urlopen_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    creds_path = tmp_path / "oauth_creds.json"
    creds_path.write_text(
        json.dumps(
            {
                "access_token": "access-token",
                "expiry_date": 9_999_999_999_999,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(antigravity_loader, "CREDS_PATH", creds_path)

    with patch("antigravity_loader.urllib.request.urlopen", side_effect=Exception("boom")):
        snapshot = antigravity_loader.load_antigravity()

    assert snapshot.used_percent is None
    assert snapshot.remaining_fraction is None
    assert snapshot.model_id is None
    assert snapshot.resets_at is None
    assert snapshot.weekly_used_percent is None
    assert snapshot.weekly_resets_at is None
    assert snapshot.polled_at is not None
