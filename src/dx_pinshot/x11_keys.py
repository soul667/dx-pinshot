"""Direct X11 hardware modifier polling via python-xlib for DX PinShot."""

from __future__ import annotations

import os
from typing import Any


class _X11ModifierMonitor:
    """Cached X11 Display connection resolving modifier masks & keycodes directly via XQueryKeymap."""

    def __init__(self) -> None:
        self._disp: Any = None
        self._failed = False
        self._keycodes: dict[str, set[int]] = {}
        self._masks: dict[str, int] = {}

    def _ensure_connected(self) -> bool:
        if self._disp is not None:
            return True
        if self._failed:
            return False
        if os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen":
            return False
        if not os.environ.get("DISPLAY", "").strip():
            return False

        try:
            from Xlib import X, XK, display

            disp = display.Display()
            mapping = disp.get_keyboard_mapping(8, 248)
            modmap = disp.get_modifier_mapping()

            def _codes_for_names(names: tuple[str, ...]) -> set[int]:
                syms = {sym for name in names if (sym := XK.string_to_keysym(name))}
                if not syms:
                    return set()
                return {idx + 8 for idx, row in enumerate(mapping) if set(row) & syms}

            alt_codes = _codes_for_names(
                ("Alt_L", "Alt_R", "Meta_L", "Meta_R", "Mode_switch", "Super_L", "Super_R")
            )
            shift_codes = _codes_for_names(("Shift_L", "Shift_R"))
            ctrl_codes = _codes_for_names(("Control_L", "Control_R"))

            if modmap:
                if len(modmap) > X.ShiftMapIndex:
                    shift_codes |= {int(c) for c in modmap[X.ShiftMapIndex] if c}
                if len(modmap) > X.ControlMapIndex:
                    ctrl_codes |= {int(c) for c in modmap[X.ControlMapIndex] if c}
                if len(modmap) > X.Mod1MapIndex:
                    alt_codes |= {int(c) for c in modmap[X.Mod1MapIndex] if c}
                if len(modmap) > X.Mod4MapIndex:
                    alt_codes |= {int(c) for c in modmap[X.Mod4MapIndex] if c}
                if len(modmap) > X.Mod5MapIndex:
                    alt_codes |= {int(c) for c in modmap[X.Mod5MapIndex] if c}

            # Fallback standard evdev X11 keycodes if XKB table was minimal
            if not alt_codes:
                alt_codes = {64, 108, 133, 134, 203, 204, 205, 206}
            if not shift_codes:
                shift_codes = {50, 62}
            if not ctrl_codes:
                ctrl_codes = {37, 105}

            self._disp = disp
            self._keycodes = {
                "alt": alt_codes,
                "shift": shift_codes,
                "ctrl": ctrl_codes,
            }
            self._masks = {
                "alt": int(X.Mod1Mask | X.Mod5Mask),
                "shift": int(X.ShiftMask),
                "ctrl": int(X.ControlMask),
            }
            return True
        except Exception:
            self._failed = True
            self._disp = None
            return False

    def is_held(self, modifier_name: str = "alt") -> bool:
        key = str(modifier_name or "alt").strip().lower()
        if key not in ("alt", "shift", "ctrl"):
            key = "alt"
        if not self._ensure_connected():
            return False

        try:
            mask = int(self._disp.screen().root.query_pointer().mask)
            if mask & self._masks[key]:
                return True

            state = self._disp.query_keymap()
            target_codes = self._keycodes[key]
            for code in target_codes:
                idx = code // 8
                if 0 <= idx < len(state) and (state[idx] & (1 << (code % 8))):
                    return True
            return False
        except Exception:
            try:
                self._disp.close()
            except Exception:
                pass
            self._disp = None
            return False


_MONITOR = _X11ModifierMonitor()


def is_hardware_modifier_held(modifier_name: str = "alt") -> bool:
    """Return True if the physical modifier key (`alt`, `shift`, `ctrl`) is held on the X11 server."""
    return _MONITOR.is_held(modifier_name)
