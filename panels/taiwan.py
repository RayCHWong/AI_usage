from __future__ import annotations

from panels.base import ThemeConfig, ThemedPanel

TAIWAN_THEME = ThemeConfig(
    id="taiwan",
    display_name="Taiwan Usage Monitor",
    icon_asset="taiwan.png",
    header_title="Taiwan Usage Monitor",
    bg_top=(0.55, 0.05, 0.08),
    bg_bottom=(0.42, 0.03, 0.05),
    card_bg=(0.30, 0.0, 0.02, 0.6),
    text_color=(1.0, 1.0, 1.0),
    muted_text_color=(1.0, 1.0, 1.0),
    primary_button_fg=(0.55, 0.05, 0.08),
    primary_button_bg=(1.0, 1.0, 1.0),
    secondary_button_fg=(1.0, 1.0, 1.0),
)


TaiwanPanel = ThemedPanel(TAIWAN_THEME)
