# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>
#
# Part of "usage". Free software licensed under the GNU Affero General Public
# License v3.0 only; see the LICENSE file for full terms and the warranty disclaimer.

from __future__ import annotations

import pytest

import talent_market_bridge


def test_list_personas_flattens_pack_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        talent_market_bridge,
        "list_state",
        lambda lang=None: {
            "packs": [
                {
                    "roles": [
                        {
                            "id": "contract-review",
                            "name": "合約審閱",
                            "personaName": "杰倫",
                            "description": "審閱合約",
                            "systemPrompt": "專業提示",
                        }
                    ]
                },
                {
                    "roles": [
                        {
                            "id": "architecture-review",
                            "name": "架構審查",
                            "personaName": "亞里",
                            "description": "審查架構",
                            "systemPrompt": "架構提示",
                        }
                    ]
                },
            ]
        },
    )

    assert talent_market_bridge.list_personas("zh-TW") == [
        {
            "id": "contract-review",
            "name": "合約審閱",
            "persona_name": "杰倫",
            "description": "審閱合約",
            "system_prompt": "專業提示",
        },
        {
            "id": "architecture-review",
            "name": "架構審查",
            "persona_name": "亞里",
            "description": "審查架構",
            "system_prompt": "架構提示",
        },
    ]


@pytest.mark.parametrize("state", [{}, {"packs": None}, {"packs": [{"roles": [None]}]}])
def test_list_personas_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch,
    state: object,
) -> None:
    monkeypatch.setattr(talent_market_bridge, "list_state", lambda lang=None: state)

    assert talent_market_bridge.list_personas() == []


def test_list_personas_swallows_list_state_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(lang: str | None = None) -> dict[str, object]:
        raise OSError("missing")

    monkeypatch.setattr(talent_market_bridge, "list_state", fail)

    assert talent_market_bridge.list_personas() == []
