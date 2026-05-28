from __future__ import annotations

from panels.base import Panel
from panels.web_panel import HTMLPanel

PANELS: tuple[Panel, ...] = (
    HTMLPanel("classic", "panel_default_name", "classic.html", codex_card_height=192.0),
    HTMLPanel("matrix", "panel_matrix", "matrix.html", height=880.0, codex_card_height=207.0),
    HTMLPanel("win95", "panel_win95", "win95.html", height=800.0, codex_card_height=173.0),
    HTMLPanel(
        "newspaper", "panel_newspaper", "newspaper.html", height=850.0, codex_card_height=197.0
    ),
    HTMLPanel(
        "cloud_observation",
        "panel_cloud_observation",
        "cloud_observation.html",
        codex_card_height=211.0,
    ),
    HTMLPanel("aquarium", "panel_aquarium", "aquarium.html", codex_card_height=211.0),
    HTMLPanel("prism_arcade", "panel_prism_arcade", "prism_arcade.html", codex_card_height=211.0),
    HTMLPanel("black_hole", "panel_black_hole", "black_hole.html", codex_card_height=211.0),
    HTMLPanel("world_cup", "panel_world_cup", "world_cup.html", codex_card_height=0.0),
)


def all_panels() -> tuple[Panel, ...]:
    return PANELS


def panel_ids() -> tuple[str, ...]:
    return tuple(panel.id for panel in PANELS)


def get_panel(panel_id: str) -> Panel:
    for panel in PANELS:
        if panel.id == panel_id:
            return panel
    return PANELS[0]
