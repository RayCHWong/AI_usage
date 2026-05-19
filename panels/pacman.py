# mypy: disable-error-code="import-untyped,misc"
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import objc
from AppKit import (
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSRectFill,
    NSTextAlignmentCenter,
    NSView,
)
from Foundation import NSMutableDictionary

from panels.base import (
    BUTTON_HEIGHT,
    BUTTON_TOP_GAP,
    CARD_HEADER_TOP,
    CARD_HEIGHT,
    CARD_RADIUS,
    CARD_ROW_GAP,
    CARD_ROW_TOP,
    CARD_SIDE_INSET,
    CONTENT_HEIGHT,
    FOOTER_GAP,
    FOOTER_HEIGHT,
    FOOTER_LINE_GAP,
    INSTALL_BUTTON_EXTRA_HEIGHT,
    PADDING,
    POPOVER_WIDTH,
    SECTION_GAP,
    PanelQuotaRowView,
    fill_rounded_rect,
    label,
    stroke_rounded_rect,
)

if TYPE_CHECKING:
    from menubar import PopoverState

# Colors
BG_COLOR = (0, 0, 0, 1)
PACMAN_YELLOW = (1.0, 0.85, 0.0, 1.0)
DIM_YELLOW = (0.85, 0.68, 0.0, 0.8)
MAZE_BLUE = (0.02, 0.02, 0.18, 0.92)  # Card BG
WALL_BLUE = (0.12, 0.12, 1.0, 0.85)  # Card Border
PROGRESS_TRACK = (0.2, 0.2, 0.2, 0.6)

SWITCH_BUTTON_WIDTH = 110.0
SWITCH_BUTTON_HEIGHT = 28.0
SWITCH_BUTTON_GAP = 16.0


def _rgba(color: tuple[float, float, float, float]) -> NSColor:
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(*color)


def _font(size: float, weight: float = 0.0) -> NSFont:
    font = NSFont.fontWithName_size_("Courier New", size)
    if font is None:
        font = NSFont.monospacedSystemFontOfSize_weight_(size, weight)
    return font


def _suffix(text: str) -> str:
    if "：" in text:
        return text.split("：", 1)[1].strip()
    if ":" in text:
        return text.split(":", 1)[1].strip()
    return text.strip()


class PacmanButton(NSButton):
    def initWithFrame_title_target_action_(
        self,
        frame: Any,
        title: str,
        target: Any,
        action: str,
    ) -> PacmanButton:
        self = objc.super(PacmanButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setTitle_(title)
        self.setBordered_(False)
        self.setTarget_(target)
        self.setAction_(action)
        return self

    def drawRect_(self, dirty_rect: Any) -> None:
        bounds = self.bounds()
        _rgba(BG_COLOR).setFill()
        path = NSBezierPath.bezierPathWithRect_(bounds)
        path.fill()

        _rgba(WALL_BLUE).setStroke()
        path.setLineWidth_(2.0)
        path.stroke()

        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(NSTextAlignmentCenter)
        attrs = NSMutableDictionary.dictionaryWithDictionary_(
            {
                NSForegroundColorAttributeName: _rgba(PACMAN_YELLOW),
                NSParagraphStyleAttributeName: style,
                NSFontAttributeName: _font(13.0, 0.3),
            },
        )
        self.title().drawInRect_withAttributes_(
            NSMakeRect(0, (bounds.size.height - 16.0) / 2, bounds.size.width, 16.0),
            attrs,
        )


class PacmanContentView(NSView):
    delegate = objc.ivar()
    claude_header = objc.ivar()
    codex_header = objc.ivar()
    claude_session = objc.ivar()
    claude_weekly = objc.ivar()
    codex_session = objc.ivar()
    codex_weekly = objc.ivar()
    rate_label = objc.ivar()
    status_label = objc.ivar()
    today_label = objc.ivar()
    switch_button = objc.ivar()
    install_hook_button = objc.ivar()
    refresh_button = objc.ivar()
    quit_button = objc.ivar()
    show_install_button = objc.ivar()

    def initWithFrame_delegate_(self, frame: Any, delegate: Any) -> PacmanContentView:
        self = objc.super(PacmanContentView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.delegate = delegate
        self.show_install_button = False

        yellow = _rgba(PACMAN_YELLOW)
        dim = _rgba(DIM_YELLOW)
        track = _rgba(PROGRESS_TRACK)

        self.claude_header = label("👻 CLAUDE CODE", _font(16.0, 0.4), yellow)
        self.codex_header = label("👾 CODEX", _font(16.0, 0.4), yellow)

        self.claude_session = PanelQuotaRowView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 56))
        self.claude_weekly = PanelQuotaRowView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 56))
        self.codex_session = PanelQuotaRowView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 56))
        self.codex_weekly = PanelQuotaRowView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 56))

        for row in (
            self.claude_session,
            self.claude_weekly,
            self.codex_session,
            self.codex_weekly,
        ):
            row.setTextColor_mutedTextColor_trackColor_(yellow, dim, track)

        self.rate_label = label("RATE: --", _font(13.5), dim)
        self.status_label = label("STATUS: LOADING", _font(13.5), dim)
        self.today_label = label("TODAY: $0.00 (0 tokens)", _font(15.0, 0.34), yellow)
        self.today_label.setAllowsDefaultTighteningForTruncation_(True)

        self.switch_button = PacmanButton.alloc().initWithFrame_title_target_action_(
            NSMakeRect(0, 0, SWITCH_BUTTON_WIDTH, SWITCH_BUTTON_HEIGHT),
            "< SWITCH >",
            delegate,
            "switchPanel:",
        )
        self.install_hook_button = PacmanButton.alloc().initWithFrame_title_target_action_(
            NSMakeRect(0, 0, 1, BUTTON_HEIGHT),
            "< INSTALL HOOK >",
            delegate,
            "installHook:",
        )
        self.install_hook_button.setHidden_(True)
        self.refresh_button = PacmanButton.alloc().initWithFrame_title_target_action_(
            NSMakeRect(0, 0, 1, BUTTON_HEIGHT),
            "< REFRESH >",
            delegate,
            "refreshNow:",
        )
        self.quit_button = PacmanButton.alloc().initWithFrame_title_target_action_(
            NSMakeRect(0, 0, 1, BUTTON_HEIGHT),
            "< EXIT >",
            delegate,
            "quitApp:",
        )

        for view in (
            self.claude_header,
            self.claude_session,
            self.claude_weekly,
            self.codex_header,
            self.codex_session,
            self.codex_weekly,
            self.rate_label,
            self.status_label,
            self.today_label,
            self.switch_button,
            self.install_hook_button,
            self.refresh_button,
            self.quit_button,
        ):
            self.addSubview_(view)
        return self

    def isFlipped(self) -> bool:
        return True

    def layout(self) -> None:
        width = self.bounds().size.width
        content_width = width - (PADDING * 2)
        card_width = content_width
        card_content_width = card_width - (CARD_SIDE_INSET * 2)
        claude_y = PADDING
        codex_y = claude_y + CARD_HEIGHT + SECTION_GAP
        footer_y = codex_y + CARD_HEIGHT + FOOTER_GAP

        text_x = PADDING + CARD_SIDE_INSET
        switch_x = PADDING + content_width - CARD_SIDE_INSET - SWITCH_BUTTON_WIDTH
        switch_y = claude_y + 18 + (36 - SWITCH_BUTTON_HEIGHT) / 2

        self.switch_button.setFrame_(
            NSMakeRect(switch_x, switch_y, SWITCH_BUTTON_WIDTH, SWITCH_BUTTON_HEIGHT),
        )
        header_text_width = switch_x - text_x - SWITCH_BUTTON_GAP
        self.claude_header.setFrame_(
            NSMakeRect(text_x, claude_y + CARD_HEADER_TOP + 1, header_text_width, 22),
        )
        self.claude_session.setFrame_(
            NSMakeRect(PADDING + CARD_SIDE_INSET, claude_y + CARD_ROW_TOP, card_content_width, 52),
        )
        self.claude_weekly.setFrame_(
            NSMakeRect(
                PADDING + CARD_SIDE_INSET,
                claude_y + CARD_ROW_TOP + CARD_ROW_GAP,
                card_content_width,
                52,
            ),
        )

        self.codex_header.setFrame_(
            NSMakeRect(text_x, codex_y + CARD_HEADER_TOP + 1, card_content_width, 22),
        )
        self.codex_session.setFrame_(
            NSMakeRect(PADDING + CARD_SIDE_INSET, codex_y + CARD_ROW_TOP, card_content_width, 52),
        )
        self.codex_weekly.setFrame_(
            NSMakeRect(
                PADDING + CARD_SIDE_INSET,
                codex_y + CARD_ROW_TOP + CARD_ROW_GAP,
                card_content_width,
                52,
            ),
        )

        self.rate_label.setFrame_(NSMakeRect(PADDING + 18, footer_y + 16, content_width - 36, 18))
        self.status_label.setFrame_(
            NSMakeRect(PADDING + 18, footer_y + 16 + FOOTER_LINE_GAP, content_width - 36, 18),
        )
        self.today_label.setFrame_(
            NSMakeRect(PADDING + 18, footer_y + 16 + FOOTER_LINE_GAP + 26, content_width - 36, 22),
        )
        y = footer_y + 16 + FOOTER_LINE_GAP + 26 + 24 + BUTTON_TOP_GAP

        button_gap = 10.0
        button_width = (content_width - 24 - button_gap) / 2
        if self.show_install_button:
            self.install_hook_button.setFrame_(
                NSMakeRect(PADDING + 12, y, content_width - 24, BUTTON_HEIGHT),
            )
            y += INSTALL_BUTTON_EXTRA_HEIGHT
        self.refresh_button.setFrame_(NSMakeRect(PADDING + 12, y, button_width, BUTTON_HEIGHT))
        self.quit_button.setFrame_(
            NSMakeRect(PADDING + 12 + button_width + button_gap, y, button_width, BUTTON_HEIGHT),
        )

    def drawRect_(self, dirty_rect: Any) -> None:
        # Background
        _rgba(BG_COLOR).setFill()
        NSRectFill(self.bounds())

        # Maze Wall Border
        maze_path = NSBezierPath.bezierPathWithRect_(self.bounds())
        _rgba((0.12, 0.12, 1.0, 0.7)).setStroke()
        maze_path.setLineWidth_(3.0)
        maze_path.stroke()

        content_width = self.bounds().size.width - (PADDING * 2)
        claude_rect = NSMakeRect(PADDING, PADDING, content_width, CARD_HEIGHT)
        codex_rect = NSMakeRect(
            PADDING,
            PADDING + CARD_HEIGHT + SECTION_GAP,
            content_width,
            CARD_HEIGHT,
        )
        footer_rect = NSMakeRect(
            PADDING,
            PADDING + (CARD_HEIGHT * 2) + SECTION_GAP + FOOTER_GAP,
            content_width,
            FOOTER_HEIGHT + (INSTALL_BUTTON_EXTRA_HEIGHT if self.show_install_button else 0.0),
        )

        for card_rect in (claude_rect, codex_rect, footer_rect):
            _rgba(MAZE_BLUE).setFill()
            fill_rounded_rect(card_rect, CARD_RADIUS)
            _rgba(WALL_BLUE).setStroke()
            stroke_rounded_rect(card_rect, CARD_RADIUS, 1.0)

        # Separators
        _rgba((0.12, 0.12, 1.0, 0.3)).setFill()
        for card_rect in (claude_rect, codex_rect):
            separator_y = card_rect.origin.y + CARD_ROW_TOP + CARD_ROW_GAP - 12
            NSRectFill(
                NSMakeRect(
                    card_rect.origin.x + CARD_SIDE_INSET,
                    separator_y,
                    card_rect.size.width - (CARD_SIDE_INSET * 2),
                    1,
                ),
            )
        NSRectFill(
            NSMakeRect(
                footer_rect.origin.x + 18,
                footer_rect.origin.y + 54,
                footer_rect.size.width - 36,
                1,
            ),
        )

    def setState_(self, state: PopoverState) -> None:
        yellow = _rgba(PACMAN_YELLOW)
        dim = _rgba(DIM_YELLOW)
        track = _rgba(PROGRESS_TRACK)

        for row_view, row_state in (
            (self.claude_session, state.claude_session),
            (self.claude_weekly, state.claude_weekly),
            (self.codex_session, state.codex_session),
            (self.codex_weekly, state.codex_weekly),
        ):
            row_view.setTextColor_mutedTextColor_trackColor_(yellow, dim, track)
            row_view.setRowState_(row_state)

        self.rate_label.setStringValue_(f"RATE: {_suffix(state.rate_text)}")
        self.status_label.setStringValue_(f"STATUS: {_suffix(state.status_text).upper()}")
        self.today_label.setStringValue_(f"TODAY: {_suffix(state.today_text)}")

        self.show_install_button = state.show_install_button
        self.install_hook_button.setHidden_(not state.show_install_button)

        self.rate_label.setTextColor_(dim)
        self.status_label.setTextColor_(dim)
        self.today_label.setTextColor_(yellow)

        self.setNeedsLayout_(True)
        self.setNeedsDisplay_(True)


class PacmanPanel:
    id = "pacman"
    display_name = "小精靈"

    def build_view(self, delegate: Any) -> NSView:
        width, height = self.preferred_size()
        return PacmanContentView.alloc().initWithFrame_delegate_(
            NSMakeRect(0, 0, width, height),
            delegate,
        )

    def apply_state(self, view: NSView, state: PopoverState) -> None:
        view.setState_(state)

    def preferred_size(self) -> tuple[float, float]:
        return (POPOVER_WIDTH, CONTENT_HEIGHT)
