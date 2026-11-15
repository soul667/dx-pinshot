"""Non-destructive GNOME Ctrl+Shift+A custom keybinding backup, swap, and restore."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
from typing import Any

MEDIA_KEYS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_KB_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
CUSTOM_KB_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
DEFAULT_BINDING = "<Primary><Alt>a"
DEFAULT_BACKUP_PATH = Path.home() / ".local" / "state" / "dx-pinshot" / "hotkey_backup.json"


def _run_gsettings(*args: str) -> str:
    proc = subprocess.run(
        ["gsettings", *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return proc.stdout.strip()


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] in {"'", '"'} and raw[-1] == raw[0]:
        try:
            val = ast.literal_eval(raw)
            if isinstance(val, str):
                return val
        except (ValueError, SyntaxError):
            pass
    return raw


def list_custom_keybindings(runner=_run_gsettings) -> list[str]:
    raw = runner("get", MEDIA_KEYS_SCHEMA, "custom-keybindings")
    if raw.startswith("@as "):
        raw = raw[4:].strip()
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return []
    if isinstance(parsed, (list, tuple)):
        return [str(item) for item in parsed]
    return []


def read_slot(slot_path: str, runner=_run_gsettings) -> dict[str, str]:
    schema = f"{CUSTOM_KB_SCHEMA}:{slot_path}"
    return {
        "path": slot_path,
        "name": _unquote(runner("get", schema, "name")),
        "command": _unquote(runner("get", schema, "command")),
        "binding": _unquote(runner("get", schema, "binding")),
    }


def write_slot(slot_path: str, name: str, command: str, binding: str, runner=_run_gsettings) -> None:
    schema = f"{CUSTOM_KB_SCHEMA}:{slot_path}"
    runner("set", schema, "name", name)
    runner("set", schema, "command", command)
    runner("set", schema, "binding", binding)


def find_slot_for_binding(binding: str = DEFAULT_BINDING, runner=_run_gsettings) -> dict[str, str] | None:
    target_norm = binding.lower()
    for slot_path in list_custom_keybindings(runner=runner):
        info = read_slot(slot_path, runner=runner)
        if info["binding"].lower() == target_norm:
            return info
    return None


def install_hotkey(
    target_command: str,
    binding: str = DEFAULT_BINDING,
    name: str = "DX PinShot 标注截图",
    backup_path: Path = DEFAULT_BACKUP_PATH,
    runner=_run_gsettings,
) -> dict[str, Any]:
    """Back up any existing shortcut bound to `<Primary><Shift>a` and bind it to DX PinShot."""
    slots = list_custom_keybindings(runner=runner)
    existing = find_slot_for_binding(binding, runner=runner)
    added_new_slot = False

    if existing is not None:
        slot_path = existing["path"]
        previous_info = existing
    else:
        slot_path = f"{CUSTOM_KB_BASE}/dx-pinshot/"
        previous_info = {"path": slot_path, "name": "", "command": "", "binding": ""}
        if slot_path not in slots:
            new_slots = [*slots, slot_path]
            runner("set", MEDIA_KEYS_SCHEMA, "custom-keybindings", repr(new_slots))
            added_new_slot = True

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if not backup_path.exists() and previous_info.get("command") != target_command:
        payload = {
            "slot_path": slot_path,
            "previous": previous_info,
            "added_new_slot": added_new_slot,
            "previous_slots": slots,
        }
        backup_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    write_slot(slot_path, name=name, command=target_command, binding=binding, runner=runner)
    return {
        "installed": True,
        "slot_path": slot_path,
        "binding": binding,
        "command": target_command,
        "backup_path": str(backup_path),
        "previous_command": previous_info.get("command", ""),
    }


def uninstall_hotkey(
    backup_path: Path = DEFAULT_BACKUP_PATH,
    runner=_run_gsettings,
) -> dict[str, Any]:
    """Restore the backed-up GNOME custom shortcut (e.g. `flameshot gui`)."""
    if not backup_path.exists():
        raise FileNotFoundError(f"No hotkey backup found at {backup_path}")

    data = json.loads(backup_path.read_text(encoding="utf-8"))
    slot_path = data["slot_path"]
    prev = data["previous"]

    if data.get("added_new_slot"):
        write_slot(slot_path, name="", command="", binding="", runner=runner)
        runner("set", MEDIA_KEYS_SCHEMA, "custom-keybindings", repr(data.get("previous_slots", [])))
    else:
        write_slot(
            slot_path,
            name=prev.get("name", ""),
            command=prev.get("command", ""),
            binding=prev.get("binding", ""),
            runner=runner,
        )

    return {
        "restored": True,
        "slot_path": slot_path,
        "restored_name": prev.get("name", ""),
        "restored_command": prev.get("command", ""),
        "restored_binding": prev.get("binding", ""),
    }


def get_hotkey_status(
    target_command: str,
    binding: str = DEFAULT_BINDING,
    backup_path: Path = DEFAULT_BACKUP_PATH,
    runner=_run_gsettings,
) -> dict[str, Any]:
    existing = find_slot_for_binding(binding, runner=runner)
    backup_data = None
    if backup_path.exists():
        try:
            backup_data = json.loads(backup_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup_data = None

    current_cmd = existing["command"] if existing else ""
    return {
        "binding": binding,
        "slot_path": existing["path"] if existing else None,
        "name": existing["name"] if existing else None,
        "command": current_cmd,
        "active_is_dx_pinshot": bool(current_cmd and (target_command in current_cmd or "dx-pinshot" in current_cmd)),
        "backup_exists": backup_path.exists(),
        "backup_path": str(backup_path),
        "backup_previous_command": (backup_data or {}).get("previous", {}).get("command") if backup_data else None,
    }
