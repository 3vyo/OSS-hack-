"""Design themes: a colour palette plus window chrome (title bar, borders, taskbar-style footer).

Each design is a Textual theme, so it also appears in the command palette's "Change theme". The
chrome for each one is in designs.tcss, keyed on the "-theme-<name>" class Textual gives the app.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from textual.theme import Theme

SETTINGS = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "linkinspector" / "settings.json"


@dataclass(frozen=True)
class Colours:
    """Colours for findings, Gemma's verdict and the risk badge, readable on the design's background."""
    danger: str
    warning: str
    info: str
    ok: str
    danger_badge: str  # Rich style for "Risk: HIGH"
    warning_badge: str  # Rich style for "Risk: MEDIUM"


@dataclass(frozen=True)
class Design:
    name: str  # also the Textual theme name
    label: str
    icon: str  # shown in the title bar
    theme: Theme
    colours: Colours


def _design(name: str, label: str, icon: str, colours: Colours, **theme) -> Design:
    return Design(name, label, icon, Theme(name=name, **theme), colours)


DESIGNS = (
    _design(
        "modern", "Modern (Windows 11 style)", "◆",
        Colours("#ff99a4", "#fce100", "#60cdff", "#6ccb5f", "bold #ffffff on #c42b1c", "bold #000000 on #fce100"),
        primary="#4cc2ff", secondary="#99ebff", accent="#60cdff", warning="#fce100", error="#ff99a4",
        success="#6ccb5f", foreground="#f3f3f3", background="#202020", surface="#272727", panel="#2d2d2d",
        dark=True, variables={"footer-background": "#1a1a1a", "footer-key-foreground": "#60cdff",
                              "link-color": "#99ebff"}),
    _design(
        "windows-7", "Windows 7 (Aero)", "◉",
        Colours("#c50f1f", "#9c5d00", "#0066cc", "#107c10", "bold #ffffff on #c50f1f", "bold #000000 on #ffcc00"),
        primary="#3399ff", secondary="#1e5799", accent="#0066cc", warning="#9c5d00", error="#c50f1f",
        success="#107c10", foreground="#1e1e1e", background="#ffffff", surface="#ffffff", panel="#f0f0f0",
        dark=False, variables={"footer-background": "#21374f", "footer-foreground": "#ffffff",
                               "footer-key-foreground": "#9fd4ff", "footer-description-foreground": "#e8f1fb",
                               "link-color": "#0066cc", "markdown-h1-color": "#1e395b",
                               "markdown-h2-color": "#1e5799", "markdown-h3-color": "#1e5799"}),
    _design(
        "windows-vista", "Windows Vista (Aero glass)", "◉",
        Colours("#ff8a80", "#ffc83d", "#6fd0ff", "#9ade7a", "bold #ffffff on #c42b1c", "bold #000000 on #ffc83d"),
        primary="#3a96dd", secondary="#7dc3f5", accent="#6fd0ff", warning="#ffc83d", error="#ff8a80",
        success="#9ade7a", foreground="#e6f0fa", background="#0b1520", surface="#102030", panel="#18304a",
        dark=True, variables={"footer-background": "#04080c", "footer-foreground": "#e6f0fa",
                              "footer-key-foreground": "#6fd0ff", "link-color": "#7dc3f5",
                              "markdown-h1-color": "#6fd0ff", "markdown-h2-color": "#7dc3f5"}),
    _design(
        "windows-xp", "Windows XP (Luna)", "▣",
        Colours("#c80000", "#8a5a00", "#0046d5", "#1b5e20", "bold #ffffff on #c80000", "bold #000000 on #ffdd55"),
        primary="#316ac5", secondary="#0054e3", accent="#3c9c33", warning="#8a5a00", error="#c80000",
        success="#2e7d32", foreground="#000000", background="#ece9d8", surface="#ffffff", panel="#ece9d8",
        dark=False, variables={"footer-background": "#245edb", "footer-foreground": "#ffffff",
                               "footer-key-foreground": "#ffffff", "footer-key-background": "#3c9c33",
                               "footer-description-foreground": "#ffffff", "link-color": "#0046d5",
                               "markdown-h1-color": "#0054e3", "markdown-h2-color": "#0054e3",
                               "markdown-h3-color": "#0054e3"}),
    _design(
        "windows-95", "Windows 95 (Classic)", "▤",
        Colours("#8b0000", "#5c4300", "#000080", "#005000", "bold #ffffff on #c00000", "bold #000000 on #ffff00"),
        primary="#000080", secondary="#008080", accent="#000080", warning="#806000", error="#c00000",
        success="#008000", foreground="#000000", background="#c0c0c0", surface="#ffffff", panel="#c0c0c0",
        dark=False, variables={"footer-background": "#c0c0c0", "footer-foreground": "#000000",
                               "footer-key-foreground": "#000080", "footer-description-foreground": "#000000",
                               "link-color": "#0000ff", "input-selection-background": "#000080",
                               "markdown-h1-color": "#000080", "markdown-h2-color": "#000080",
                               "markdown-h3-color": "#000080", "scrollbar": "#c0c0c0",
                               "scrollbar-background": "#dfdfdf"}),
    _design(
        "high-contrast", "High contrast", "■",
        Colours("#ff8c8c", "#ffff00", "#1aebff", "#3ff23f", "bold #000000 on #ff8c8c", "bold #000000 on #ffff00"),
        primary="#1aebff", secondary="#ffff00", accent="#ffff00", warning="#ffff00", error="#ff8c8c",
        success="#3ff23f", foreground="#ffffff", background="#000000", surface="#000000", panel="#000000",
        dark=True, variables={"footer-background": "#000000", "footer-key-foreground": "#ffff00",
                              "border": "#ffffff", "link-color": "#ffff00", "markdown-h1-color": "#ffffff",
                              "markdown-h2-color": "#ffffff", "markdown-h3-color": "#ffffff"}),
    _design(
        "retro-terminal", "Retro terminal", ">_",
        Colours("#ff5f5f", "#ffb000", "#7fffd4", "#33ff66", "bold #000000 on #ff5f5f", "bold #000000 on #ffb000"),
        primary="#33ff66", secondary="#ffb000", accent="#00ff7f", warning="#ffb000", error="#ff5f5f",
        success="#33ff66", foreground="#33ff66", background="#000000", surface="#000000", panel="#021402",
        dark=True, variables={"footer-background": "#000000", "footer-key-foreground": "#ffb000",
                              "link-color": "#00ff7f", "markdown-h1-color": "#33ff66",
                              "markdown-h2-color": "#33ff66", "markdown-h3-color": "#33ff66"}),
)
BY_NAME = {design.name: design for design in DESIGNS}
DEFAULT = "modern"


def colours_for(theme: Theme) -> Colours:
    """A design's own colours, or ones taken from any other Textual theme."""
    if theme.name in BY_NAME:
        return BY_NAME[theme.name].colours
    danger, warning = theme.error or "red", theme.warning or "yellow"
    return Colours(danger, warning, theme.accent or theme.primary, theme.success or "green",
                   f"bold white on {danger}", f"bold black on {warning}")


def load_choice() -> str | None:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8")).get("design")
    except (OSError, ValueError, AttributeError):
        return None


def save_choice(name: str) -> None:
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps({"design": name}), encoding="utf-8")
    except OSError:
        pass  # remembering the design is a convenience; never fail because of it
