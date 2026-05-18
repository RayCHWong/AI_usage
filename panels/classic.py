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
    NSStrokeColorAttributeName,
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
    CLAUDE_COLOR,
    CODEX_COLOR,
    CONTENT_HEIGHT,
    FOOTER_GAP,
    FOOTER_HEIGHT,
    FOOTER_LINE_GAP,
    INSTALL_BUTTON_EXTRA_HEIGHT,
    PADDING,
    POPOVER_WIDTH,
    SECTION_GAP,
    PanelActionButton,
    PanelHeaderIconView,
    PanelQuotaRowView,
    background_gradient_for_view,
    card_border_color_for_view,
    card_fill_color_for_view,
    card_separator_color_for_view,
    fill_rounded_rect,
    label,
    muted_label_color,
    ns_color,
    regular_font,
    resolve_resource,
    semibold_font,
    stroke_rounded_rect,
)

if TYPE_CHECKING:
    from menubar import PopoverState

SWITCH_BUTTON_WIDTH = 106.0
SWITCH_BUTTON_HEIGHT = 28.0
SWITCH_BUTTON_GAP = 16.0


class ClassicSwitchButton(NSButton):
    def initWithFrame_target_action_(
        self,
        frame: Any,
        target: Any,
        action: str,
    ) -> ClassicSwitchButton:
        self = objc.super(ClassicSwitchButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setTitle_("⇄ 更換面板")
        self.setBordered_(False)
        self.setTarget_(target)
        self.setAction_(action)
        return self

    def drawRect_(self, dirty_rect: Any) -> None:
        bounds = self.bounds()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 9.0, 9.0)
        if _is_dark_appearance(self):
            fill = NSColor.whiteColor().colorWithAlphaComponent_(0.075)
            border = NSColor.whiteColor().colorWithAlphaComponent_(0.2)
            text = NSColor.whiteColor().colorWithAlphaComponent_(0.88)
        else:
            fill = NSColor.blackColor().colorWithAlphaComponent_(0.045)
            border = NSColor.blackColor().colorWithAlphaComponent_(0.14)
            text = NSColor.labelColor().colorWithAlphaComponent_(0.88)
        fill.setFill()
        path.fill()
        border.setStroke()
        path.setLineWidth_(1.0)
        path.stroke()

        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(1)
        attrs = NSMutableDictionary.dictionaryWithDictionary_(
            {
                NSForegroundColorAttributeName: text,
                NSParagraphStyleAttributeName: style,
                NSStrokeColorAttributeName: NSColor.clearColor(),
                NSFontAttributeName: NSFont.systemFontOfSize_weight_(12.5, 0.28),
            },
        )
        self.title().drawInRect_withAttributes_(
            NSMakeRect(0, 7.0, bounds.size.width, 16.0),
            attrs,
        )


def _is_dark_appearance(view: NSView) -> bool:
    name = view.effectiveAppearance().name() or ""
    return "Dark" in name


class ClassicContentView(NSView):
    delegate = objc.ivar()
    claude_icon = objc.ivar()
    codex_icon = objc.ivar()
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

    def initWithFrame_delegate_(self, frame: Any, delegate: Any) -> ClassicContentView:
        self = objc.super(ClassicContentView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.delegate = delegate
        self.show_install_button = False
        claude_accent = ns_color(CLAUDE_COLOR)
        codex_accent = ns_color(CODEX_COLOR)
        self.claude_icon = PanelHeaderIconView.alloc().initWithFrame_color_path_(
            NSMakeRect(0, 0, 42, 42),
            claude_accent,
            resolve_resource("claude.webp"),
        )
        self.codex_icon = PanelHeaderIconView.alloc().initWithFrame_color_path_(
            NSMakeRect(0, 0, 42, 42),
            codex_accent,
            resolve_resource("codex.webp"),
        )
        self.claude_header = label("Claude Code", semibold_font(), NSColor.labelColor())
        self.codex_header = label("Codex", semibold_font(), NSColor.labelColor())
        self.claude_session = PanelQuotaRowView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 56))
        self.claude_weekly = PanelQuotaRowView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 56))
        self.codex_session = PanelQuotaRowView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 56))
        self.codex_weekly = PanelQuotaRowView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 56))
        self.rate_label = label("速率：--", regular_font(13.5), muted_label_color())
        self.status_label = label("狀態：載入中", regular_font(13.5), muted_label_color())
        self.today_label = label(
            "今日：$0.00 (0 tokens)",
            NSFont.systemFontOfSize_weight_(15, 0.34),
            NSColor.labelColor(),
        )
        self.today_label.setAllowsDefaultTighteningForTruncation_(True)
        self.switch_button = ClassicSwitchButton.alloc().initWithFrame_target_action_(
            NSMakeRect(0, 0, SWITCH_BUTTON_WIDTH, SWITCH_BUTTON_HEIGHT),
            delegate,
            "switchPanel:",
        )
        self.install_hook_button = (
            PanelActionButton.alloc().initWithFrame_title_primary_color_target_action_(
                NSMakeRect(0, 0, 1, BUTTON_HEIGHT),
                "立即安裝 hook",
                True,
                claude_accent,
                delegate,
                "installHook:",
            )
        )
        self.install_hook_button.setHidden_(True)
        self.refresh_button = (
            PanelActionButton.alloc().initWithFrame_title_primary_color_target_action_(
                NSMakeRect(0, 0, 1, BUTTON_HEIGHT),
                "立即更新",
                True,
                codex_accent,
                delegate,
                "refreshNow:",
            )
        )
        self.quit_button = (
            PanelActionButton.alloc().initWithFrame_title_primary_color_target_action_(
                NSMakeRect(0, 0, 1, BUTTON_HEIGHT),
                "結束",
                False,
                None,
                delegate,
                "quitApp:",
            )
        )

        for view in (
            self.claude_icon,
            self.codex_icon,
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
        icon_x = PADDING + CARD_SIDE_INSET

        switch_x = PADDING + content_width - CARD_SIDE_INSET - SWITCH_BUTTON_WIDTH
        switch_y = claude_y + 18 + (36 - SWITCH_BUTTON_HEIGHT) / 2
        self.switch_button.setFrame_(
            NSMakeRect(switch_x, switch_y, SWITCH_BUTTON_WIDTH, SWITCH_BUTTON_HEIGHT),
        )
        header_text_x = icon_x + 48
        header_text_width = switch_x - header_text_x - SWITCH_BUTTON_GAP
        self.claude_icon.setFrame_(NSMakeRect(icon_x, claude_y + 18, 36, 36))
        self.claude_header.setFrame_(
            NSMakeRect(header_text_x, claude_y + CARD_HEADER_TOP + 1, header_text_width, 22),
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

        self.codex_icon.setFrame_(NSMakeRect(icon_x, codex_y + 18, 36, 36))
        self.codex_header.setFrame_(
            NSMakeRect(icon_x + 48, codex_y + CARD_HEADER_TOP + 1, card_content_width - 48, 22),
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
        background_gradient_for_view(self).drawInRect_angle_(self.bounds(), 90.0)
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
            card_fill_color_for_view(self).setFill()
            fill_rounded_rect(card_rect, CARD_RADIUS)
            card_border_color_for_view(self).setStroke()
            stroke_rounded_rect(card_rect, CARD_RADIUS, 1.0)

        card_separator_color_for_view(self).setFill()
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
        self.claude_session.setRowState_(state.claude_session)
        self.claude_weekly.setRowState_(state.claude_weekly)
        self.codex_session.setRowState_(state.codex_session)
        self.codex_weekly.setRowState_(state.codex_weekly)
        self.rate_label.setStringValue_(state.rate_text)
        self.status_label.setStringValue_(state.status_text)
        self.today_label.setStringValue_(state.today_text)
        self.show_install_button = state.show_install_button
        self.install_hook_button.setHidden_(not state.show_install_button)
        self.rate_label.setTextColor_(muted_label_color())
        self.status_label.setTextColor_(muted_label_color())
        self.today_label.setTextColor_(NSColor.labelColor())
        self.setNeedsLayout_(True)
        self.setNeedsDisplay_(True)


class ClassicPanel:
    id = "classic"
    display_name = "預設"

    def build_view(self, delegate: Any) -> NSView:
        width, height = self.preferred_size()
        return ClassicContentView.alloc().initWithFrame_delegate_(
            NSMakeRect(0, 0, width, height),
            delegate,
        )

    def apply_state(self, view: NSView, state: PopoverState) -> None:
        view.setState_(state)

    def preferred_size(self) -> tuple[float, float]:
        return (POPOVER_WIDTH, CONTENT_HEIGHT)
