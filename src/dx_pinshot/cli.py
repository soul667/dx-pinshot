"""Command-line interface for DX PinShot (`gui`, `demo-render`, `install-hotkey`, `uninstall-hotkey`, `status`)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys

from PyQt5 import QtGui, QtWidgets

from .clipboard import DEFAULT_STATE_DIR, run_clipboard_holder
from .hotkey import DEFAULT_BINDING, get_hotkey_status, install_hotkey, uninstall_hotkey
from .overlay import PinShotPopup, ScreenCropSelector, render_demo_screens

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BIN_CMD = f"{PACKAGE_ROOT / 'bin' / 'dx-pinshot'} gui"


def _is_dx_pinshot_pid(pid: int) -> bool:
    cmdline_file = Path(f"/proc/{pid}/cmdline")
    if not cmdline_file.exists():
        return False
    try:
        cmdline = cmdline_file.read_bytes().decode("utf-8", errors="ignore")
    except OSError:
        return False
    return "dx_pinshot" in cmdline or "dx-pinshot" in cmdline


def _toggle_existing_instance(pid_file: Path) -> bool:
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid_file.unlink(missing_ok=True)
        return False
    if pid != os.getpid() and _is_dx_pinshot_pid(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            pass
    pid_file.unlink(missing_ok=True)
    return False


def _cmd_gui(args: argparse.Namespace) -> int:
    pid_file = DEFAULT_STATE_DIR / "gui.pid"
    DEFAULT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    if _toggle_existing_instance(pid_file):
        return 0

    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
        holder: dict[str, PinShotPopup] = {}
        selector = ScreenCropSelector.capture_desktop(theme_name=args.theme)

        def _open_popup(cropped_pixmap: QtGui.QPixmap) -> None:
            popup = PinShotPopup(cropped_pixmap=cropped_pixmap, theme_name=args.theme)
            holder["popup"] = popup

            cursor_pos = QtGui.QCursor.pos()
            target_screen = app.screenAt(cursor_pos) or app.primaryScreen()
            if target_screen is not None:
                avail = target_screen.availableGeometry()
                px = avail.left() + max(0, (avail.width() - popup.width()) // 2)
                py = avail.top() + max(0, (avail.height() - popup.height()) // 2)
                popup.move(px, py)

            selector.hide()
            selector.close()
            app.processEvents()

            popup.show()
            popup.raise_()
            popup.activateWindow()
            popup.setFocus()

        selector.regionSelected.connect(_open_popup)
        selector.showFullScreen()
        selector.activateWindow()
        selector.raise_()
        selector.setFocus()
        return app.exec_()
    finally:
        if pid_file.exists():
            try:
                if pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    pid_file.unlink(missing_ok=True)
            except OSError:
                pass


def _cmd_demo_render(args: argparse.Namespace) -> int:
    paths = render_demo_screens(Path(args.out), theme_name=args.theme)
    for path in paths:
        print(path)
    return 0


def _cmd_tui(args: argparse.Namespace) -> int:
    from .tui import run_tui

    return run_tui(initial_tab=args.tab)


def _cmd_config(args: argparse.Namespace) -> int:
    from .config import (
        CONFIG_KEY_HELP,
        get_config_path,
        load_config,
        set_config_value,
    )

    action = getattr(args, "config_action", None) or "list"
    if action == "path":
        print(get_config_path())
        return 0
    if action == "list":
        print(json.dumps(load_config(), indent=2, ensure_ascii=False))
        return 0
    if action == "keys":
        print(json.dumps(CONFIG_KEY_HELP, indent=2, ensure_ascii=False))
        return 0
    if action == "get":
        cfg = load_config()
        if args.key not in cfg:
            print(json.dumps({"error": f"Unknown key: {args.key}", "valid_keys": list(cfg.keys())}, ensure_ascii=False))
            return 2
        print(json.dumps({args.key: cfg[args.key]}, ensure_ascii=False))
        return 0
    if action == "set":
        try:
            updated = set_config_value(args.key, args.value)
        except KeyError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps(updated, indent=2, ensure_ascii=False))
        return 0
    return 1


def _cmd_install_hotkey(args: argparse.Namespace) -> int:
    from .config import load_config

    binding = args.binding or load_config().get("capture_hotkey", DEFAULT_BINDING)
    info = install_hotkey(target_command=args.command, binding=binding)
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def _cmd_uninstall_hotkey(_args: argparse.Namespace) -> int:
    info = uninstall_hotkey()
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from .config import get_config_path, get_state_dir, load_config

    cfg = load_config()
    state_dir = get_state_dir()
    binding = args.binding or str(cfg.get("capture_hotkey", DEFAULT_BINDING))
    hotkey_info = get_hotkey_status(
        target_command=args.command,
        binding=binding,
        backup_path=state_dir / "hotkey_backup.json",
    )
    latest_png = state_dir / "latest.png"
    latest_md = state_dir / "latest.md"
    payload = {
        "bin": str(PACKAGE_ROOT / "bin" / "dx-pinshot"),
        "config_path": str(get_config_path()),
        "config": cfg,
        "state_dir": str(state_dir),
        "latest_png": str(latest_png) if latest_png.exists() else None,
        "latest_md": str(latest_md) if latest_md.exists() else None,
        "hotkey": hotkey_info,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _cmd_clip_holder(args: argparse.Namespace) -> int:
    return run_clipboard_holder(image_path=args.image, text_file=args.text_file, ready_fd=args.ready_fd)


def _cmd_paste_both(args: argparse.Namespace) -> int:
    from .clipboard import paste_latest_both

    res = paste_latest_both(delay_sec=args.delay)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dx-pinshot",
        description="DX PinShot — minimalist Set-of-Mark screenshot & grouped pin annotator.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    p_gui = sub.add_parser("gui", help="Launch fullscreen capture & Set-of-Mark annotation overlay.")
    p_gui.add_argument("--theme", choices=["auto", "paper", "obsidian"], default=None)
    p_gui.set_defaults(func=_cmd_gui)

    p_tui = sub.add_parser("tui", help="Open DX PinShot Textual console (captures history & settings).")
    p_tui.add_argument("--tab", choices=["captures", "settings"], default="captures")
    p_tui.set_defaults(func=_cmd_tui)

    p_cfg = sub.add_parser("config", help="Read or update ~/.config/dx-pinshot/settings.json.")
    cfg_sub = p_cfg.add_subparsers(dest="config_action")
    p_cfg_list = cfg_sub.add_parser("list", help="Print normalized configuration as JSON.")
    p_cfg_list.set_defaults(func=_cmd_config)
    p_cfg_path = cfg_sub.add_parser("path", help="Print settings.json path.")
    p_cfg_path.set_defaults(func=_cmd_config)
    p_cfg_keys = cfg_sub.add_parser("keys", help="Describe available configuration keys.")
    p_cfg_keys.set_defaults(func=_cmd_config)
    p_cfg_get = cfg_sub.add_parser("get", help="Read a single configuration key.")
    p_cfg_get.add_argument("key")
    p_cfg_get.set_defaults(func=_cmd_config)
    p_cfg_set = cfg_sub.add_parser("set", help="Set and save a single configuration key.")
    p_cfg_set.add_argument("key")
    p_cfg_set.add_argument("value")
    p_cfg_set.set_defaults(func=_cmd_config)
    p_cfg.set_defaults(func=_cmd_config)

    p_demo = sub.add_parser("demo-render", help="Render offscreen visual QA PNG demos.")
    p_demo.add_argument("--out", required=True, help="Output directory for demo PNG screenshots.")
    p_demo.add_argument("--theme", choices=["auto", "paper", "obsidian"], default="paper")
    p_demo.set_defaults(func=_cmd_demo_render)

    p_install = sub.add_parser("install-hotkey", help="Bind Ctrl+Shift+A in GNOME to dx-pinshot (with backup).")
    p_install.add_argument("--binding", default=None)
    p_install.add_argument("--command", default=DEFAULT_BIN_CMD)
    p_install.set_defaults(func=_cmd_install_hotkey)

    p_uninstall = sub.add_parser("uninstall-hotkey", help="Restore the original backed-up Ctrl+Shift+A binding.")
    p_uninstall.set_defaults(func=_cmd_uninstall_hotkey)

    p_status = sub.add_parser("status", help="Inspect hotkey binding, backup status, and state artifacts.")
    p_status.add_argument("--binding", default=None)
    p_status.add_argument("--command", default=DEFAULT_BIN_CMD)
    p_status.set_defaults(func=_cmd_status)

    p_paste = sub.add_parser("paste-both", help="Paste latest SoM image + Markdown instructions into focused window.")
    p_paste.add_argument("--delay", type=float, default=0.38)
    p_paste.set_defaults(func=_cmd_paste_both)

    p_holder = sub.add_parser("_clip_holder", help=argparse.SUPPRESS)
    p_holder.add_argument("--image", default=None)
    p_holder.add_argument("--text-file", default=None)
    p_holder.add_argument("--ready-fd", type=int, default=None)
    p_holder.set_defaults(func=_cmd_clip_holder)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "subcommand", None):
        args = parser.parse_args(["gui"])
    return int(args.func(args))
