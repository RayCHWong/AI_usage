# mypy: disable-error-code="import-untyped,misc"
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import objc
from AppKit import (
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGradient,
    NSImage,
    NSMakeRect,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSRectFill,
    NSStrokeColorAttributeName,
    NSTextAlignmentRight,
    NSTextField,
    NSView,
)
from Foundation import NSBundle, NSUserDefaults

if TYPE_CHECKING:
    from menubar import PopoverState


POPOVER_WIDTH = 364.0
CONTENT_HEIGHT = 574.0
HEADER_HEIGHT = 98.0
PADDING = 14.0
TRACK_HEIGHT = 8.0
CARD_HEIGHT = 184.0
FOOTER_HEIGHT = 152.0
CARD_RADIUS = 18.0
CARD_HEADER_TOP = 22.0
CARD_ROW_TOP = 66.0
CARD_ROW_GAP = 64.0
CARD_SIDE_INSET = 18.0
SECTION_GAP = 14.0
FOOTER_GAP = 12.0
FOOTER_LINE_GAP = 18.0
BUTTON_TOP_GAP = 18.0
BUTTON_HEIGHT = 32.0
INSTALL_BUTTON_EXTRA_HEIGHT = BUTTON_HEIGHT + 10.0
CLAUDE_COLOR = (244 / 255, 145 / 255, 100 / 255)
CODEX_COLOR = (88 / 255, 214 / 255, 230 / 255)
WARN_COLOR = (255 / 255, 196 / 255, 57 / 255)
DANGER_COLOR = (255 / 255, 69 / 255, 58 / 255)
ACTIVE_PANEL_DEFAULTS_KEY = "usage.activePanelId"


class Panel(Protocol):
    id: str
    display_name: str

    def build_view(self, delegate: Any) -> NSView: ...

    def apply_state(self, view: NSView, state: PopoverState) -> None: ...

    def preferred_size(self) -> tuple[float, float]: ...


@dataclass(frozen=True)
class ThemeConfig:
    id: str
    display_name: str
    icon_asset: str
    header_title: str
    bg_top: tuple[float, float, float]
    bg_bottom: tuple[float, float, float]
    card_bg: tuple[float, float, float, float]
    text_color: tuple[float, float, float]
    muted_text_color: tuple[float, float, float]
    primary_button_fg: tuple[float, float, float]
    primary_button_bg: tuple[float, float, float]
    secondary_button_fg: tuple[float, float, float]


def load_active_panel_id(defaults: Any | None = None) -> str:
    store = defaults if defaults is not None else NSUserDefaults.standardUserDefaults()
    value = store.stringForKey_(ACTIVE_PANEL_DEFAULTS_KEY)
    return str(value) if value else "classic"


def save_active_panel_id(panel_id: str, defaults: Any | None = None) -> None:
    store = defaults if defaults is not None else NSUserDefaults.standardUserDefaults()
    store.setObject_forKey_(panel_id, ACTIVE_PANEL_DEFAULTS_KEY)
    if hasattr(store, "synchronize"):
        store.synchronize()


def resolve_resource(name: str) -> str:
    bundle = NSBundle.mainBundle()
    if bundle is not None:
        stem, _, ext = name.rpartition(".")
        path = bundle.pathForResource_ofType_(stem, ext)
        if path:
            return str(path)
    return str(Path(__file__).resolve().parent.parent / "assets" / name)


def ns_color(rgb: tuple[float, float, float], alpha: float = 1.0) -> NSColor:
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgb, alpha)


def ns_color_rgba(rgba: tuple[float, float, float, float]) -> NSColor:
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgba)


class PanelProgressBarView(NSView):
    percent = objc.ivar()
    bar_color = objc.ivar()
    available = objc.ivar()
    track_color = objc.ivar()

    def initWithFrame_(self, frame: Any) -> PanelProgressBarView:
        self = objc.super(PanelProgressBarView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.percent = None
        self.bar_color = NSColor.secondaryLabelColor()
        self.available = False
        self.track_color = None
        return self

    def isFlipped(self) -> bool:
        return True

    def setTrackColor_(self, color: NSColor | None) -> None:
        self.track_color = color
        self.setNeedsDisplay_(True)

    def setPercent_color_available_(
        self,
        percent: float | None,
        color: NSColor,
        available: bool,
    ) -> None:
        self.percent = percent
        self.bar_color = color
        self.available = available
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty_rect: Any) -> None:
        bounds = self.bounds()
        rect = NSMakeRect(
            0,
            (bounds.size.height - TRACK_HEIGHT) / 2,
            bounds.size.width,
            TRACK_HEIGHT,
        )

        if not self.available or self.percent is None:
            (
                self.track_color or NSColor.secondaryLabelColor().colorWithAlphaComponent_(0.3)
            ).setFill()
            fill_rounded_rect(rect, TRACK_HEIGHT / 2)
            return

        (self.track_color or track_color_for_view(self)).setFill()
        fill_rounded_rect(rect, TRACK_HEIGHT / 2)

        pct = max(0.0, min(100.0, float(self.percent)))
        fill_width = min(bounds.size.width, max(2.0, bounds.size.width * pct / 100.0))
        fill_rect = NSMakeRect(rect.origin.x, rect.origin.y, fill_width, rect.size.height)
        accent_gradient(self.bar_color).drawInBezierPath_angle_(
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                fill_rect,
                TRACK_HEIGHT / 2,
                TRACK_HEIGHT / 2,
            ),
            0.0,
        )


class PanelQuotaRowView(NSView):
    title_label = objc.ivar()
    percent_label = objc.ivar()
    reset_label = objc.ivar()
    progress_bar = objc.ivar()
    text_color = objc.ivar()
    muted_text_color = objc.ivar()

    def initWithFrame_(self, frame: Any) -> PanelQuotaRowView:
        self = objc.super(PanelQuotaRowView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.text_color = NSColor.labelColor()
        self.muted_text_color = muted_label_color()
        self.title_label = label("", medium_font(), self.text_color)
        self.percent_label = label(
            "",
            NSFont.systemFontOfSize_weight_(12, 0.31),
            self.text_color,
            NSTextAlignmentRight,
        )
        self.reset_label = label("", regular_font(11), self.muted_text_color, NSTextAlignmentRight)
        self.progress_bar = PanelProgressBarView.alloc().initWithFrame_(
            NSMakeRect(0, 20, 1, TRACK_HEIGHT),
        )
        for view in (self.title_label, self.percent_label, self.progress_bar, self.reset_label):
            self.addSubview_(view)
        return self

    def isFlipped(self) -> bool:
        return True

    def setTextColor_mutedTextColor_trackColor_(
        self,
        text_color: NSColor,
        muted_text_color: NSColor,
        track_color: NSColor | None,
    ) -> None:
        self.text_color = text_color
        self.muted_text_color = muted_text_color
        self.title_label.setTextColor_(text_color)
        self.reset_label.setTextColor_(muted_text_color)
        self.progress_bar.setTrackColor_(track_color)

    def layout(self) -> None:
        width = self.bounds().size.width
        self.title_label.setFrame_(NSMakeRect(0, 0, width * 0.42, 18))
        self.percent_label.setFrame_(NSMakeRect(width * 0.42, 0, width * 0.58, 18))
        self.progress_bar.setFrame_(NSMakeRect(0, 24, width, TRACK_HEIGHT))
        self.reset_label.setFrame_(NSMakeRect(0, 38, width, 14))

    def setRowState_(self, row: Any) -> None:
        self.title_label.setStringValue_(row.title)
        self.percent_label.setStringValue_(row.percent_text)
        self.reset_label.setStringValue_(row.reset_text)
        color = NSColor.colorWithCalibratedRed_green_blue_alpha_(*row.color, 1.0)
        self.progress_bar.setPercent_color_available_(row.percent, color, row.available)
        self.percent_label.setTextColor_(color if row.available else self.muted_text_color)
        self.reset_label.setTextColor_(self.muted_text_color)
        self.title_label.setTextColor_(self.text_color)
        self.setNeedsLayout_(True)


class PanelHeaderIconView(NSView):
    accent_color = objc.ivar()
    image = objc.ivar()

    def initWithFrame_color_path_(
        self,
        frame: Any,
        color: NSColor,
        path: str,
    ) -> PanelHeaderIconView:
        self = objc.super(PanelHeaderIconView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.accent_color = color
        self.image = NSImage.alloc().initWithContentsOfFile_(path)
        return self

    def isFlipped(self) -> bool:
        return True

    def drawRect_(self, dirty_rect: Any) -> None:
        bounds = self.bounds()
        if self.image is not None:
            self.image.drawInRect_(bounds)


class PanelThemedIconView(NSView):
    image = objc.ivar()

    def initWithFrame_path_(self, frame: Any, path: str) -> PanelThemedIconView:
        self = objc.super(PanelThemedIconView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.image = NSImage.alloc().initWithContentsOfFile_(path)
        return self

    def isFlipped(self) -> bool:
        return True

    def drawRect_(self, dirty_rect: Any) -> None:
        bounds = self.bounds()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.2, 0.62, 1.0).setFill()
        NSRectFill(bounds)
        if self.image is not None:
            if bounds.size.width >= 120:
                image_rect = NSMakeRect(-16, -12, bounds.size.width + 32, bounds.size.height + 24)
                self.image.drawInRect_(image_rect)
            else:
                self.image.drawInRect_(bounds)


class PanelActionButton(NSButton):
    accent_color = objc.ivar()
    is_primary = objc.ivar()

    def initWithFrame_title_primary_color_target_action_(
        self,
        frame: Any,
        title: str,
        primary: bool,
        color: NSColor | None,
        target: Any,
        action: str,
    ) -> PanelActionButton:
        self = objc.super(PanelActionButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self.accent_color = color
        self.is_primary = primary
        self.setTitle_(title)
        self.setFont_(NSFont.systemFontOfSize_weight_(14, 0.28))
        self.setBordered_(False)
        self.setTarget_(target)
        self.setAction_(action)
        return self

    def drawRect_(self, dirty_rect: Any) -> None:
        bounds = self.bounds()
        radius = 10.0
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, radius, radius)
        border = button_border_color(self, self.is_primary)
        border.setStroke()

        if self.is_primary and self.accent_color is not None:
            button_gradient(self.accent_color).drawInBezierPath_angle_(path, 90.0)
        else:
            secondary_button_fill_color(self).setFill()
            path.fill()

        path.setLineWidth_(1.0)
        path.stroke()
        draw_button_title(self, bounds)


class PanelThemeActionButton(NSButton):
    fill_color = objc.ivar()
    title_color = objc.ivar()
    border_color = objc.ivar()

    def initWithFrame_title_fill_titleColor_border_target_action_(
        self,
        frame: Any,
        title: str,
        fill_color: NSColor,
        title_color: NSColor,
        border_color: NSColor,
        target: Any,
        action: str,
    ) -> PanelThemeActionButton:
        self = objc.super(PanelThemeActionButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self.fill_color = fill_color
        self.title_color = title_color
        self.border_color = border_color
        self.setTitle_(title)
        self.setFont_(NSFont.systemFontOfSize_weight_(14, 0.32))
        self.setBordered_(False)
        self.setTarget_(target)
        self.setAction_(action)
        return self

    def drawRect_(self, dirty_rect: Any) -> None:
        bounds = self.bounds()
        radius = min(10.0, bounds.size.height / 3)
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, radius, radius)
        self.fill_color.setFill()
        path.fill()
        self.border_color.setStroke()
        path.setLineWidth_(1.0)
        path.stroke()
        title_y = (bounds.size.height - 18.0) / 2
        font_size = 18.0 if bounds.size.height >= 40.0 and bounds.size.width >= 120.0 else 14.0
        draw_text_in_rect(self.title(), bounds, self.title_color, font_size, 0.36, title_y)


class ThemedContentView(NSView):
    config = objc.ivar()
    delegate = objc.ivar()
    header_icon = objc.ivar()
    header_label = objc.ivar()
    switch_button = objc.ivar()
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
    install_hook_button = objc.ivar()
    refresh_button = objc.ivar()
    quit_button = objc.ivar()
    show_install_button = objc.ivar()

    def initWithFrame_config_delegate_(
        self,
        frame: Any,
        config: ThemeConfig,
        delegate: Any,
    ) -> ThemedContentView:
        self = objc.super(ThemedContentView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.config = config
        self.delegate = delegate
        self.show_install_button = False
        text = ns_color(config.text_color)
        muted = ns_color(config.muted_text_color, 0.7)
        track = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.0, 0.01, 0.82)
        claude_accent = ns_color(CLAUDE_COLOR)
        codex_accent = ns_color(CODEX_COLOR)
        self.header_icon = PanelThemedIconView.alloc().initWithFrame_path_(
            NSMakeRect(0, 0, 88, 88),
            resolve_resource(config.icon_asset),
        )
        self.header_label = label(
            config.header_title, NSFont.systemFontOfSize_weight_(23, 0.7), text
        )
        self.switch_button = (
            PanelThemeActionButton.alloc().initWithFrame_title_fill_titleColor_border_target_action_(
                NSMakeRect(0, 0, 88, 38),
                "⇄ 更換",
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.35, 0.0, 0.02, 0.72),
                text,
                NSColor.whiteColor().colorWithAlphaComponent_(0.45),
                delegate,
                "switchPanel:",
            )
        )
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
        self.claude_header = label("Claude Code", semibold_font(), text)
        self.codex_header = label("Codex", semibold_font(), text)
        self.claude_session = themed_row(text, muted, track)
        self.claude_weekly = themed_row(text, muted, track)
        self.codex_session = themed_row(text, muted, track)
        self.codex_weekly = themed_row(text, muted, track)
        self.rate_label = label("速率：--", regular_font(13.5), muted)
        self.status_label = label("狀態：載入中", regular_font(13.5), muted)
        self.today_label = label(
            "今日：$0.00 (0 tokens)", NSFont.systemFontOfSize_weight_(15, 0.34), text
        )
        self.today_label.setAllowsDefaultTighteningForTruncation_(True)
        primary_fg = ns_color(config.primary_button_fg)
        primary_bg = ns_color(config.primary_button_bg)
        secondary_fg = ns_color(config.secondary_button_fg)
        secondary_bg = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.38, 0.0, 0.02, 0.55)
        self.install_hook_button = (
            PanelThemeActionButton.alloc().initWithFrame_title_fill_titleColor_border_target_action_(
                NSMakeRect(0, 0, 1, BUTTON_HEIGHT),
                "立即安裝 hook",
                primary_bg,
                primary_fg,
                NSColor.whiteColor().colorWithAlphaComponent_(0.78),
                delegate,
                "installHook:",
            )
        )
        self.install_hook_button.setHidden_(True)
        self.refresh_button = (
            PanelThemeActionButton.alloc().initWithFrame_title_fill_titleColor_border_target_action_(
                NSMakeRect(0, 0, 1, BUTTON_HEIGHT),
                "立即更新",
                primary_bg,
                primary_fg,
                NSColor.whiteColor().colorWithAlphaComponent_(0.78),
                delegate,
                "refreshNow:",
            )
        )
        self.quit_button = (
            PanelThemeActionButton.alloc().initWithFrame_title_fill_titleColor_border_target_action_(
                NSMakeRect(0, 0, 1, BUTTON_HEIGHT),
                "結束",
                secondary_bg,
                secondary_fg,
                NSColor.whiteColor().colorWithAlphaComponent_(0.68),
                delegate,
                "quitApp:",
            )
        )

        views: tuple[Any, ...]
        if config.header_title:
            views = (self.header_icon, self.header_label, self.switch_button)
        else:
            views = (self.switch_button,)
        for view in views + (
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
            self.install_hook_button,
            self.refresh_button,
            self.quit_button,
        ):
            self.addSubview_(view)
        return self

    def isFlipped(self) -> bool:
        return True

    def header_offset(self) -> float:
        return HEADER_HEIGHT if self.config.header_title else 0.0

    def layout(self) -> None:
        width = self.bounds().size.width
        content_width = width - (PADDING * 2)
        card_width = content_width
        card_content_width = card_width - (CARD_SIDE_INSET * 2)
        offset = self.header_offset()
        claude_y = PADDING + offset
        codex_y = claude_y + CARD_HEIGHT + SECTION_GAP
        footer_y = codex_y + CARD_HEIGHT + FOOTER_GAP
        icon_x = PADDING + CARD_SIDE_INSET

        if self.config.header_title:
            self.header_icon.setFrame_(NSMakeRect(PADDING, 10, 88, 88))
            self.header_label.setFrame_(NSMakeRect(PADDING + 100, 37, 138, 34))
            self.switch_button.setFrame_(NSMakeRect(width - PADDING - 88, 35, 88, 38))
        else:
            self.switch_button.setFrame_(NSMakeRect(width - PADDING - 108, PADDING, 108, 28))

        self.claude_icon.setFrame_(NSMakeRect(icon_x, claude_y + 18, 36, 36))
        self.claude_header.setFrame_(
            NSMakeRect(icon_x + 48, claude_y + CARD_HEADER_TOP + 1, card_content_width - 48, 22)
        )
        self.claude_session.setFrame_(
            NSMakeRect(PADDING + CARD_SIDE_INSET, claude_y + CARD_ROW_TOP, card_content_width, 52)
        )
        self.claude_weekly.setFrame_(
            NSMakeRect(
                PADDING + CARD_SIDE_INSET,
                claude_y + CARD_ROW_TOP + CARD_ROW_GAP,
                card_content_width,
                52,
            )
        )
        self.codex_icon.setFrame_(NSMakeRect(icon_x, codex_y + 18, 36, 36))
        self.codex_header.setFrame_(
            NSMakeRect(icon_x + 48, codex_y + CARD_HEADER_TOP + 1, card_content_width - 48, 22)
        )
        self.codex_session.setFrame_(
            NSMakeRect(PADDING + CARD_SIDE_INSET, codex_y + CARD_ROW_TOP, card_content_width, 52)
        )
        self.codex_weekly.setFrame_(
            NSMakeRect(
                PADDING + CARD_SIDE_INSET,
                codex_y + CARD_ROW_TOP + CARD_ROW_GAP,
                card_content_width,
                52,
            )
        )
        self.rate_label.setFrame_(NSMakeRect(PADDING + 18, footer_y + 16, content_width - 36, 18))
        self.status_label.setFrame_(
            NSMakeRect(PADDING + 18, footer_y + 16 + FOOTER_LINE_GAP, content_width - 36, 18)
        )
        self.today_label.setFrame_(
            NSMakeRect(PADDING + 18, footer_y + 16 + FOOTER_LINE_GAP + 26, content_width - 36, 22)
        )
        y = footer_y + 16 + FOOTER_LINE_GAP + 26 + 24 + BUTTON_TOP_GAP

        button_gap = 10.0
        button_width = (content_width - 24 - button_gap) / 2
        if self.show_install_button:
            self.install_hook_button.setFrame_(
                NSMakeRect(PADDING + 12, y, content_width - 24, BUTTON_HEIGHT)
            )
            y += INSTALL_BUTTON_EXTRA_HEIGHT
        self.refresh_button.setFrame_(NSMakeRect(PADDING + 12, y, button_width, BUTTON_HEIGHT))
        self.quit_button.setFrame_(
            NSMakeRect(PADDING + 12 + button_width + button_gap, y, button_width, BUTTON_HEIGHT)
        )

    def drawRect_(self, dirty_rect: Any) -> None:
        NSGradient.alloc().initWithColors_(
            [ns_color(self.config.bg_top), ns_color(self.config.bg_bottom)]
        ).drawInRect_angle_(self.bounds(), 90.0)
        content_width = self.bounds().size.width - (PADDING * 2)
        offset = self.header_offset()
        claude_rect = NSMakeRect(PADDING, PADDING + offset, content_width, CARD_HEIGHT)
        codex_rect = NSMakeRect(
            PADDING, PADDING + offset + CARD_HEIGHT + SECTION_GAP, content_width, CARD_HEIGHT
        )
        footer_rect = NSMakeRect(
            PADDING,
            PADDING + offset + (CARD_HEIGHT * 2) + SECTION_GAP + FOOTER_GAP,
            content_width,
            FOOTER_HEIGHT + (INSTALL_BUTTON_EXTRA_HEIGHT if self.show_install_button else 0.0),
        )

        for card_rect in (claude_rect, codex_rect, footer_rect):
            ns_color_rgba(self.config.card_bg).setFill()
            fill_rounded_rect(card_rect, CARD_RADIUS)
            NSColor.whiteColor().colorWithAlphaComponent_(0.13).setStroke()
            stroke_rounded_rect(card_rect, CARD_RADIUS, 1.0)

        NSColor.whiteColor().colorWithAlphaComponent_(0.18).setFill()
        for card_rect in (claude_rect, codex_rect):
            separator_y = card_rect.origin.y + CARD_ROW_TOP + CARD_ROW_GAP - 12
            NSRectFill(
                NSMakeRect(
                    card_rect.origin.x + CARD_SIDE_INSET,
                    separator_y,
                    card_rect.size.width - (CARD_SIDE_INSET * 2),
                    1,
                )
            )
        NSRectFill(
            NSMakeRect(
                footer_rect.origin.x + 18, footer_rect.origin.y + 54, footer_rect.size.width - 36, 1
            )
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
        muted = ns_color(self.config.muted_text_color, 0.7)
        text = ns_color(self.config.text_color)
        self.rate_label.setTextColor_(muted)
        self.status_label.setTextColor_(muted)
        self.today_label.setTextColor_(text)
        self.setNeedsLayout_(True)
        self.setNeedsDisplay_(True)


class ThemedPanel:
    id: str
    display_name: str

    def __init__(self, config: ThemeConfig) -> None:
        self.config = config
        self.id = config.id
        self.display_name = config.display_name

    def build_view(self, delegate: Any) -> NSView:
        width, height = self.preferred_size()
        return ThemedContentView.alloc().initWithFrame_config_delegate_(
            NSMakeRect(0, 0, width, height),
            self.config,
            delegate,
        )

    def apply_state(self, view: NSView, state: PopoverState) -> None:
        view.setState_(state)

    def preferred_size(self) -> tuple[float, float]:
        return (
            POPOVER_WIDTH,
            CONTENT_HEIGHT + (HEADER_HEIGHT if self.config.header_title else 0.0),
        )


def themed_row(text: NSColor, muted: NSColor, track: NSColor) -> PanelQuotaRowView:
    row = PanelQuotaRowView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 56))
    row.setTextColor_mutedTextColor_trackColor_(text, muted, track)
    return cast(PanelQuotaRowView, row)


def label(text: str, font: NSFont, color: NSColor, alignment: int | None = None) -> NSTextField:
    field = NSTextField.labelWithString_(text)
    field.setFont_(font)
    field.setTextColor_(color)
    field.setDrawsBackground_(False)
    field.setBordered_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    if alignment is not None:
        field.setAlignment_(alignment)
    return field


def semibold_font() -> NSFont:
    return NSFont.systemFontOfSize_weight_(16, 0.33)


def medium_font() -> NSFont:
    return NSFont.systemFontOfSize_weight_(12.5, 0.28)


def regular_font(size: float) -> NSFont:
    return NSFont.systemFontOfSize_weight_(size, -0.4)


def muted_label_color() -> NSColor:
    return NSColor.labelColor().colorWithAlphaComponent_(0.74)


def is_dark_appearance(view: NSView) -> bool:
    name = view.effectiveAppearance().name() or ""
    return "Dark" in name


def card_fill_color_for_view(view: NSView) -> NSColor:
    if is_dark_appearance(view):
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(0.115, 0.126, 0.15, 0.92)
    return NSColor.whiteColor().colorWithAlphaComponent_(0.9)


def card_border_color_for_view(view: NSView) -> NSColor:
    if is_dark_appearance(view):
        return NSColor.whiteColor().colorWithAlphaComponent_(0.08)
    return NSColor.blackColor().colorWithAlphaComponent_(0.08)


def card_separator_color_for_view(view: NSView) -> NSColor:
    if is_dark_appearance(view):
        return NSColor.whiteColor().colorWithAlphaComponent_(0.09)
    return NSColor.blackColor().colorWithAlphaComponent_(0.08)


def track_color_for_view(view: NSView) -> NSColor:
    if is_dark_appearance(view):
        return NSColor.whiteColor().colorWithAlphaComponent_(0.07)
    return NSColor.blackColor().colorWithAlphaComponent_(0.057)


def background_gradient_for_view(view: NSView) -> NSGradient:
    if is_dark_appearance(view):
        colors = [
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.1, 0.11, 0.135, 1.0),
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.065, 0.072, 0.09, 1.0),
        ]
    else:
        colors = [
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.96, 0.965, 0.975, 1.0),
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.92, 0.93, 0.945, 1.0),
        ]
    return NSGradient.alloc().initWithColors_(colors)


def accent_gradient(color: NSColor) -> NSGradient:
    return NSGradient.alloc().initWithColors_([color.highlightWithLevel_(0.22), color])


def secondary_button_fill_color(view: NSView) -> NSColor:
    if is_dark_appearance(view):
        return NSColor.whiteColor().colorWithAlphaComponent_(0.045)
    return NSColor.blackColor().colorWithAlphaComponent_(0.035)


def button_gradient(color: NSColor) -> NSGradient:
    return NSGradient.alloc().initWithColors_(
        [color.highlightWithLevel_(0.08), color.shadowWithLevel_(0.12)]
    )


def button_border_color(view: NSView, primary: bool) -> NSColor:
    if primary:
        return NSColor.whiteColor().colorWithAlphaComponent_(0.14)
    if is_dark_appearance(view):
        return NSColor.whiteColor().colorWithAlphaComponent_(0.12)
    return NSColor.blackColor().colorWithAlphaComponent_(0.1)


def draw_button_title(button: PanelActionButton, bounds: Any) -> None:
    text_color = (
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.12, 0.18, 0.2, 0.96)
        if button.is_primary
        else NSColor.labelColor()
    )
    draw_text_in_rect(button.title(), bounds, text_color, 14, 0.32, 8)


def draw_text_in_rect(
    text: str,
    bounds: Any,
    text_color: NSColor,
    size: float,
    weight: float,
    y: float,
) -> None:
    style = NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(1)
    attrs = {
        NSForegroundColorAttributeName: text_color,
        NSParagraphStyleAttributeName: style,
        NSStrokeColorAttributeName: NSColor.clearColor(),
        NSFontAttributeName: NSFont.systemFontOfSize_weight_(size, weight),
    }
    drawable = cast(Any, text)
    drawable.drawInRect_withAttributes_(NSMakeRect(0, y, bounds.size.width, 18), attrs)


def fill_rounded_rect(rect: Any, radius: float) -> None:
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, radius, radius).fill()


def stroke_rounded_rect(rect: Any, radius: float, width: float) -> None:
    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, radius, radius)
    path.setLineWidth_(width)
    path.stroke()
