"""DX PinShot design tokens mirroring DX Writing's minimalist Paper & Obsidian themes."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5 import QtGui

SERIF_FAMILIES = [
    "Newsreader",
    "Noto Serif CJK SC",
    "Noto Serif",
    "Source Han Serif SC",
    "Georgia",
    "serif",
]

MONO_FAMILIES = [
    "JetBrains Mono",
    "DejaVu Sans Mono",
    "Noto Sans Mono",
    "monospace",
]

SERIF_CSS = '"Newsreader", "Noto Serif CJK SC", "Noto Serif", Georgia, serif'
MONO_CSS = '"JetBrains Mono", "DejaVu Sans Mono", "Noto Sans Mono", monospace'


@dataclass(frozen=True)
class Theme:
    name: str
    bg_page: str
    bg_panel: str
    bg_subtle: str
    text_primary: str
    text_secondary: str
    text_muted: str
    border_hairline: str
    border_strong: str
    anchor_line: str
    anchor_tint: str
    anchor_tint_strong: str
    redline_del: str
    redline_ins: str
    pin_fill: str
    pin_text: str
    pin_halo: str
    scrim_rgba: tuple[int, int, int, int]


PAPER_THEME = Theme(
    name="paper",
    bg_page="#FAF9F5",
    bg_panel="#FFFFFF",
    bg_subtle="#F3F1EA",
    text_primary="#191817",
    text_secondary="#575551",
    text_muted="#8C8982",
    border_hairline="#E6E2D8",
    border_strong="#9E988E",
    anchor_line="#B89B67",
    anchor_tint="rgba(196, 164, 108, 0.14)",
    anchor_tint_strong="rgba(196, 164, 108, 0.27)",
    redline_del="#9E2A2B",
    redline_ins="#2D6A4F",
    pin_fill="#191817",
    pin_text="#FAF9F5",
    pin_halo="#FFFFFF",
    scrim_rgba=(25, 24, 23, 104),
)

OBSIDIAN_THEME = Theme(
    name="obsidian",
    bg_page="#121211",
    bg_panel="#1A1A18",
    bg_subtle="#21201E",
    text_primary="#ECEAE4",
    text_secondary="#A3A098",
    text_muted="#6E6B64",
    border_hairline="#2B2A27",
    border_strong="#6A665E",
    anchor_line="#9E8558",
    anchor_tint="rgba(196, 164, 108, 0.14)",
    anchor_tint_strong="rgba(196, 164, 108, 0.27)",
    redline_del="#E07A7A",
    redline_ins="#74C69D",
    pin_fill="#ECEAE4",
    pin_text="#121211",
    pin_halo="#121211",
    scrim_rgba=(12, 12, 11, 148),
)


def get_theme(name: str = "paper") -> Theme:
    if (name or "").strip().lower() == "auto":
        from .config import resolve_theme_name

        name = resolve_theme_name("auto")
    return OBSIDIAN_THEME if (name or "").strip().lower() == "obsidian" else PAPER_THEME


@dataclass(frozen=True)
class PinTheme:
    """Visual styling for Set-of-Mark pin badges (translucent fill + crisp border & text)."""

    name: str
    fill_rgba: tuple[int, int, int, int]
    text_hex: str
    border_hex: str
    active_hex: str
    halo_rgba: tuple[int, int, int, int]


PIN_THEMES: dict[str, PinTheme] = {
    "translucent-ink": PinTheme(
        name="translucent-ink",
        fill_rgba=(25, 24, 23, 184),
        text_hex="#FAF9F5",
        border_hex="#9E988E",
        active_hex="#D4AF6A",
        halo_rgba=(255, 255, 255, 196),
    ),
    "translucent-paper": PinTheme(
        name="translucent-paper",
        fill_rgba=(250, 249, 245, 204),
        text_hex="#191817",
        border_hex="#575551",
        active_hex="#C85A32",
        halo_rgba=(25, 24, 23, 160),
    ),
    "vermilion": PinTheme(
        name="vermilion",
        fill_rgba=(200, 90, 50, 196),
        text_hex="#FFFFFF",
        border_hex="#FFFFFF",
        active_hex="#F4D35E",
        halo_rgba=(25, 24, 23, 168),
    ),
    "cobalt": PinTheme(
        name="cobalt",
        fill_rgba=(47, 111, 237, 196),
        text_hex="#FFFFFF",
        border_hex="#FFFFFF",
        active_hex="#F4D35E",
        halo_rgba=(25, 24, 23, 168),
    ),
}


def get_pin_theme(name: str = "translucent-ink", ui_theme: Theme | None = None) -> PinTheme:
    key = (name or "translucent-ink").strip().lower()
    if key == "auto":
        is_dark = ui_theme is not None and ui_theme.name == "obsidian"
        key = "translucent-paper" if is_dark else "translucent-ink"
    return PIN_THEMES.get(key, PIN_THEMES["translucent-ink"])


def _apply_families(font: QtGui.QFont, families: list[str], style_hint: QtGui.QFont.StyleHint) -> QtGui.QFont:
    font.setStyleHint(style_hint)
    if hasattr(font, "setFamilies"):
        font.setFamilies(families)
    else:
        font.setFamily(families[1] if len(families) > 1 else families[0])
    return font


def serif_font(point_size: float = 10.5, weight: int = QtGui.QFont.Normal, italic: bool = False) -> QtGui.QFont:
    font = QtGui.QFont()
    _apply_families(font, SERIF_FAMILIES, QtGui.QFont.Serif)
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    font.setItalic(italic)
    return font


def mono_font(point_size: float = 9.0, weight: int = QtGui.QFont.Normal) -> QtGui.QFont:
    font = QtGui.QFont()
    _apply_families(font, MONO_FAMILIES, QtGui.QFont.TypeWriter)
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    return font


def build_widget_qss(theme: Theme) -> str:
    """Global stylesheet fragments keeping DX Writing's sharp 0px hairlines & editorial typography."""
    return f"""
    QWidget {{
        font-family: {SERIF_CSS};
        color: {theme.text_primary};
    }}
    QToolTip {{
        background-color: {theme.bg_panel};
        color: {theme.text_primary};
        border: 1px solid {theme.border_strong};
        border-radius: 0px;
        padding: 4px 8px;
        font-family: {MONO_CSS};
        font-size: 11px;
    }}
    QPlainTextEdit {{
        background-color: {theme.bg_panel};
        color: {theme.text_primary};
        border: none;
        border-radius: 0px;
        selection-background-color: {theme.anchor_tint_strong};
        selection-color: {theme.text_primary};
        font-family: {SERIF_CSS};
        font-size: 13px;
    }}
    QScrollBar:vertical {{
        background: {theme.bg_page};
        width: 6px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {theme.border_hairline};
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {theme.border_strong};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    """
