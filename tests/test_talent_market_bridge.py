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
                    "id": "solo-law-firm",
                    "name": "律師事務所",
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
                    "id": "solo-software-studio",
                    "name": "軟體工作室",
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
            "pack_id": "solo-law-firm",
            "pack_name": "律師事務所",
        },
        {
            "id": "architecture-review",
            "name": "架構審查",
            "persona_name": "亞里",
            "description": "審查架構",
            "system_prompt": "架構提示",
            "pack_id": "solo-software-studio",
            "pack_name": "軟體工作室",
        },
    ]


def test_list_personas_keeps_roles_when_pack_identity_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = {
        "id": "contract-review",
        "name": "合約審閱",
        "personaName": "杰倫",
        "description": "審閱合約",
        "systemPrompt": "專業提示",
    }
    monkeypatch.setattr(
        talent_market_bridge,
        "list_state",
        lambda lang=None: {
            "packs": [
                {"name": "律師事務所", "roles": [role]},
                {"id": "solo-law-firm", "roles": [role]},
            ]
        },
    )

    personas = talent_market_bridge.list_personas()

    assert len(personas) == 2
    assert all(persona["pack_id"] == "" for persona in personas)
    assert all(persona["pack_name"] == "" for persona in personas)


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
