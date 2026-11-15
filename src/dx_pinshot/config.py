"""Configuration management for DX PinShot (~/.config/dx-pinshot/settings.json)."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

VALID_THEMES = ("auto", "paper", "obsidian")
VALID_PIN_THEMES = ("translucent-ink", "translucent-paper", "vermilion", "cobalt", "auto")
VALID_MODIFIERS = ("alt", "shift", "ctrl")
VALID_EXPORT_MODES = ("both", "image", "markdown")

DEFAULT_CONFIG: dict[str, Any] = {
    "theme": "paper",
    "pin_theme": "translucent-ink",
    "multi_select_modifier": "alt",
    "export_mode": "both",
    "capture_hotkey": "<Primary><Alt>a",
    "pin_scale": 1.0,
    "show_leader_lines": True,
    "keep_history_count": 20,
}

CONFIG_KEY_HELP: dict[str, str] = {
    "theme": "UI theme (auto | paper | obsidian)",
    "pin_theme": "Set-of-Mark pin badge theme (translucent-ink | translucent-paper | vermilion | cobalt | auto)",
    "multi_select_modifier": "Modifier key held for grouped N.1/N.2 multi-select clicks (alt | shift | ctrl)",
    "export_mode": "Default clipboard/export payload (both | image | markdown)",
    "capture_hotkey": "GNOME global custom shortcut binding (e.g. <Primary><Alt>a)",
    "pin_scale": "Scale factor for numbered SoM pin badges (0.75 - 1.5)",
    "show_leader_lines": "Draw dotted leader lines between grouped multi-select pins (true | false)",
    "keep_history_count": "Maximum number of saved capture artifacts kept in state history (1 - 200)",
}


def get_config_dir(config_dir: Path | str | None = None) -> Path:
    """Return the DX PinShot configuration directory."""
    if config_dir is not None:
        return Path(config_dir).expanduser()
    env_dir = os.environ.get("DX_PINSHOT_CONFIG_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "dx-pinshot"


def get_state_dir(state_dir: Path | str | None = None) -> Path:
    """Return the DX PinShot state directory (history + hotkey backup)."""
    if state_dir is not None:
        return Path(state_dir).expanduser()
    env_dir = os.environ.get("DX_PINSHOT_STATE_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / "dx-pinshot"


def get_config_path(config_dir: Path | str | None = None) -> Path:
    """Return the settings.json path inside the config directory."""
    return get_config_dir(config_dir) / "settings.json"


def _ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def parse_bool(value: Any, default: bool = False) -> bool:
    """Parse loose boolean inputs from CLI/JSON safely."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "y"}:
            return True
        if text in {"0", "false", "no", "off", "n"}:
            return False
    return default


def normalize_config(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize configuration mapping with defaults."""
    cfg = dict(DEFAULT_CONFIG)
    if not isinstance(raw, Mapping):
        return cfg

    theme = str(raw.get("theme", cfg["theme"])).strip().lower()
    if theme in VALID_THEMES:
        cfg["theme"] = theme

    modifier = str(raw.get("multi_select_modifier", cfg["multi_select_modifier"])).strip().lower()
    if modifier in {"control", "primary"}:
        modifier = "ctrl"
    if modifier in VALID_MODIFIERS:
        cfg["multi_select_modifier"] = modifier

    export_mode = str(raw.get("export_mode", cfg["export_mode"])).strip().lower()
    if export_mode in VALID_EXPORT_MODES:
        cfg["export_mode"] = export_mode

    pin_theme = str(raw.get("pin_theme", cfg["pin_theme"])).strip().lower()
    if pin_theme in VALID_PIN_THEMES:
        cfg["pin_theme"] = pin_theme

    hotkey = str(raw.get("capture_hotkey", cfg["capture_hotkey"])).strip()
    if hotkey:
        cfg["capture_hotkey"] = hotkey

    try:
        scale = float(raw.get("pin_scale", cfg["pin_scale"]))
        if 0.75 <= scale <= 1.5:
            cfg["pin_scale"] = round(scale, 2)
        else:
            cfg["pin_scale"] = round(max(0.75, min(1.5, scale)), 2)
    except (TypeError, ValueError):
        pass

    cfg["show_leader_lines"] = parse_bool(
        raw.get("show_leader_lines", cfg["show_leader_lines"]),
        default=bool(DEFAULT_CONFIG["show_leader_lines"]),
    )

    try:
        keep_count = int(raw.get("keep_history_count", cfg["keep_history_count"]))
        cfg["keep_history_count"] = max(1, min(200, keep_count))
    except (TypeError, ValueError):
        pass

    return cfg


def load_config(config_dir: Path | str | None = None) -> dict[str, Any]:
    """Load settings.json from disk, falling back cleanly to defaults."""
    path = get_config_path(config_dir)
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    return normalize_config(raw)


def save_config(data: Mapping[str, Any], config_dir: Path | str | None = None) -> Path:
    """Normalize and atomically persist settings.json with 0600 permissions."""
    target_dir = _ensure_private_dir(get_config_dir(config_dir))
    path = target_dir / "settings.json"
    normalized = normalize_config(data)
    tmp_path = target_dir / f".settings.{os.getpid()}.tmp"
    tmp_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    os.replace(tmp_path, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def set_config_value(key: str, value: Any, config_dir: Path | str | None = None) -> dict[str, Any]:
    """Update a single known configuration key and persist settings.json."""
    if key not in DEFAULT_CONFIG:
        valid_keys = ", ".join(DEFAULT_CONFIG.keys())
        raise KeyError(f"Unknown config key {key!r}. Valid keys: {valid_keys}")
    current = load_config(config_dir)
    current[key] = value
    normalized = normalize_config(current)
    save_config(normalized, config_dir=config_dir)
    return normalized


def detect_system_theme_name() -> str:
    """Detect GNOME light/dark preference and map to 'paper' or 'obsidian'."""
    try:
        proc = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        if proc.returncode == 0 and "prefer-dark" in proc.stdout.lower():
            return "obsidian"
    except (OSError, subprocess.SubprocessError):
        pass
    return "paper"


def resolve_theme_name(theme_value: str | None) -> str:
    """Resolve 'auto'/'paper'/'obsidian' into concrete 'paper' or 'obsidian'."""
    key = (theme_value or "paper").strip().lower()
    if key == "auto":
        return detect_system_theme_name()
    if key == "obsidian":
        return "obsidian"
    return "paper"


def modifier_display_label(modifier_name: str | None) -> str:
    """Return user-facing keycap text for the configured multi-select modifier."""
    key = (modifier_name or "alt").strip().lower()
    if key in {"ctrl", "control", "primary"}:
        return "Ctrl"
    if key == "shift":
        return "Shift"
    return "Alt"
