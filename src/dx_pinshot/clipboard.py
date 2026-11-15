"""File persistence and X11/Wayland clipboard export for DX PinShot."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import html
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import webbrowser

from PyQt5 import QtCore, QtGui, QtWidgets

from .config import get_state_dir as _config_state_dir, load_config
from .web_workbench import build_workbench_html

DEFAULT_STATE_DIR = _config_state_dir()


@dataclass
class ExportResult:
    latest_png: Path
    latest_raw_png: Path
    latest_md: Path
    history_png: Path
    history_md: Path
    clipboard_status: str


def get_state_dir(state_dir: Path | None = None) -> Path:
    path = _config_state_dir(state_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prune_history(history_dir: Path, keep: int = 20) -> None:
    pngs = sorted(history_dir.glob("pinshot_*.png"), reverse=True)
    for old_png in pngs[max(1, keep):]:
        old_png.unlink(missing_ok=True)
        old_md = old_png.with_suffix(".md")
        old_md.unlink(missing_ok=True)


def save_artifacts(
    som_image: QtGui.QImage,
    raw_image: QtGui.QImage,
    prompt_markdown: str,
    state_dir: Path | None = None,
    keep_history: int | None = None,
) -> tuple[Path, Path, Path, Path, Path]:
    """Persist `latest.png`, `latest_raw.png`, `latest.md`, and timestamped history files."""
    root = get_state_dir(state_dir)
    history_dir = root / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    latest_png = root / "latest.png"
    latest_raw_png = root / "latest_raw.png"
    latest_md = root / "latest.md"

    som_image.save(str(latest_png), "PNG")
    raw_image.save(str(latest_raw_png), "PNG")
    latest_md.write_text(prompt_markdown + "\n", encoding="utf-8")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    history_png = history_dir / f"pinshot_{stamp}.png"
    history_md = history_dir / f"pinshot_{stamp}.md"
    som_image.save(str(history_png), "PNG")
    history_md.write_text(prompt_markdown + "\n", encoding="utf-8")
    keep = int(keep_history) if keep_history is not None else int(load_config().get("keep_history_count", 20))
    _prune_history(history_dir, keep=keep)

    return latest_png, latest_raw_png, latest_md, history_png, history_md


def _spawn_x11_qt_holder(image_path: Path | None, text_path: Path | None) -> bool:
    """Spawn a background Qt clipboard owner so X11 serves clipboard targets after GUI closes."""
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return False
    if not os.environ.get("DISPLAY"):
        return False

    src_dir = str(Path(__file__).resolve().parents[1])
    env = os.environ.copy()
    prev_py = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{prev_py}" if prev_py else src_dir

    r_fd, w_fd = os.pipe()
    cmd = [sys.executable, "-m", "dx_pinshot", "_clip_holder", "--ready-fd", str(w_fd)]
    if image_path is not None:
        cmd.extend(["--image", str(image_path)])
    if text_path is not None:
        cmd.extend(["--text-file", str(text_path)])

    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(w_fd,),
            start_new_session=True,
        )
    except OSError:
        os.close(r_fd)
        os.close(w_fd)
        return False

    os.close(w_fd)
    try:
        import select

        ready, _, _ = select.select([r_fd], [], [], 1.5)
        if ready:
            data = os.read(r_fd, 16)
            return data == b"1"
        return proc.poll() is None
    except OSError:
        return proc.poll() is None
    finally:
        os.close(r_fd)


def _normalize_export_mode(mode: str | None) -> str:
    key = (mode or "both").strip().lower()
    if key == "markdown":
        return "prompt"
    if key in {"both", "image", "prompt"}:
        return key
    return "both"


def _copy_via_cli_tool(image_path: Path | None, text_path: Path | None, mode: str) -> str:
    mode = _normalize_export_mode(mode)
    is_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    if is_wayland and shutil.which("wl-copy"):
        if mode in {"image", "both"} and image_path is not None:
            with image_path.open("rb") as fh:
                subprocess.run(
                    ["wl-copy", "--type", "image/png"],
                    stdin=fh,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
            return "wayland:wl-copy(image/png)"
        if mode == "prompt" and text_path is not None:
            with text_path.open("rb") as fh:
                subprocess.run(
                    ["wl-copy", "--type", "text/plain;charset=utf-8"],
                    stdin=fh,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
            return "wayland:wl-copy(text/plain)"

    if shutil.which("xclip"):
        if mode == "image" and image_path is not None:
            subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-i", str(image_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                timeout=5,
                check=True,
            )
            return "x11:xclip(image/png)"
        if mode == "prompt" and text_path is not None:
            subprocess.run(
                ["xclip", "-selection", "clipboard", "-i", str(text_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                timeout=5,
                check=True,
            )
            return "x11:xclip(text/plain)"

    return "file-only"


def _copy_single_clipboard(image_path: Path | None, text_path: Path | None, mode: str) -> str:
    """Copy a single payload (`image` or `prompt`) via Qt X11 holder (for large PNGs) or CLI tools."""
    is_wayland = bool(os.environ.get("WAYLAND_DISPLAY")) and shutil.which("wl-copy") is not None
    if mode == "image" and not is_wayland:
        # Qt's X11 clipboard implements full async INCR transfer for >1MB PNGs where `xclip` can stall.
        if _spawn_x11_qt_holder(image_path, None):
            return "x11:qt-holder(image/png)"
    try:
        status = _copy_via_cli_tool(image_path, text_path, mode)
        if status != "file-only":
            return status
    except subprocess.SubprocessError:
        pass
    if _spawn_x11_qt_holder(image_path, text_path):
        label = "image/png" if mode == "image" else "text/plain"
        return f"x11:qt-holder({label})"
    return "saved-only"


def export_session(
    som_image: QtGui.QImage,
    raw_image: QtGui.QImage,
    prompt_markdown: str,
    mode: str = "both",
    state_dir: Path | None = None,
    copy_clipboard: bool = True,
    keep_history: int | None = None,
) -> ExportResult:
    """Save SoM PNG + VLM Markdown and optionally populate system clipboard (`both` | `image` | `prompt`/`markdown`)."""
    norm_mode = _normalize_export_mode(mode)
    latest_png, latest_raw_png, latest_md, history_png, history_md = save_artifacts(
        som_image=som_image,
        raw_image=raw_image,
        prompt_markdown=prompt_markdown,
        state_dir=state_dir,
        keep_history=keep_history,
    )

    clip_status = "saved-only"
    if copy_clipboard and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        if norm_mode == "both":
            # Push text first, pause briefly so clipboard history (e.g. dx-clip) captures it,
            # then push clean image/png so direct Ctrl+V pastes the screenshot reliably.
            text_ok = False
            if prompt_markdown.strip():
                text_status = _copy_single_clipboard(None, latest_md, "prompt")
                text_ok = text_status != "saved-only"
                if text_ok:
                    time.sleep(0.22)
            img_status = _copy_single_clipboard(latest_png, None, "image")
            clip_status = f"{text_status}+{img_status}" if text_ok and img_status != "saved-only" else img_status
        elif norm_mode == "image":
            clip_status = _copy_single_clipboard(latest_png, None, "image")
        elif norm_mode == "prompt":
            clip_status = _copy_single_clipboard(None, latest_md, "prompt")

    return ExportResult(
        latest_png=latest_png,
        latest_raw_png=latest_raw_png,
        latest_md=latest_md,
        history_png=history_png,
        history_md=history_md,
        clipboard_status=clip_status,
    )


def run_clipboard_holder(
    image_path: str | None = None,
    text_file: str | None = None,
    timeout_sec: int = 1800,
    ready_fd: int | None = None,
) -> int:
    """Background windowless Qt app serving `image/png` + `text/plain` until X11 clipboard ownership changes."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    clipboard = app.clipboard()
    mime = QtCore.QMimeData()

    has_payload = False
    if image_path:
        img = QtGui.QImage(image_path)
        if not img.isNull():
            mime.setImageData(img)
            has_payload = True
    if text_file:
        text_path = Path(text_file)
        if text_path.exists():
            mime.setText(text_path.read_text(encoding="utf-8"))
            has_payload = True

    if not has_payload:
        if ready_fd is not None:
            try:
                os.write(ready_fd, b"0")
                os.close(ready_fd)
            except OSError:
                pass
        return 1

    clipboard.setMimeData(mime)
    app.processEvents()
    if ready_fd is not None:
        try:
            os.write(ready_fd, b"1")
            os.close(ready_fd)
        except OSError:
            pass

    armed = False

    def _arm() -> None:
        nonlocal armed
        armed = True

    def _on_change() -> None:
        if armed and not clipboard.ownsClipboard():
            app.quit()

    clipboard.dataChanged.connect(_on_change)
    QtCore.QTimer.singleShot(800, _arm)
    QtCore.QTimer.singleShot(timeout_sec * 1000, app.quit)
    return app.exec_()


def export_html_preview(
    som_image: QtGui.QImage,
    raw_image: QtGui.QImage,
    prompt_markdown: str,
    *,
    threads_data: list[dict] | None = None,
    theme_name: str = "paper",
    pin_theme: str = "translucent-ink",
    pin_scale: float = 1.0,
    show_leader_lines: bool = True,
    state_dir: Path | None = None,
    keep_history: int | None = None,
    open_browser: bool = True,
) -> Path:
    """Save artifacts + a self-contained DX-styled interactive HTML annotation workbench (`latest.html`)."""
    latest_png, latest_raw, _latest_md, _hist_png, _hist_md = save_artifacts(
        som_image=som_image,
        raw_image=raw_image,
        prompt_markdown=prompt_markdown,
        state_dir=state_dir,
        keep_history=keep_history,
    )
    raw_b64 = base64.b64encode(latest_raw.read_bytes()).decode("ascii")
    html_doc = build_workbench_html(
        raw_png_b64=raw_b64,
        crop_width=max(1, raw_image.width()),
        crop_height=max(1, raw_image.height()),
        threads_data=list(threads_data or []),
        theme_name=theme_name,
        pin_theme=pin_theme,
        pin_scale=pin_scale,
        show_leader_lines=show_leader_lines,
    )
    html_path = latest_png.parent / "latest.html"
    html_path.write_text(html_doc, encoding="utf-8")

    if open_browser and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        uri = html_path.resolve().as_uri()
        opened = False
        try:
            opened = bool(webbrowser.open(uri, new=2))
        except Exception:
            opened = False
        if not opened and shutil.which("xdg-open"):
            subprocess.Popen(
                ["xdg-open", uri],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    return html_path


def paste_latest_both(state_dir: Path | None = None, delay_sec: float = 0.38) -> dict[str, object]:
    """Paste latest Set-of-Mark PNG followed by latest Markdown instructions into the focused window."""
    root = get_state_dir(state_dir)
    latest_png = root / "latest.png"
    latest_md = root / "latest.md"

    xdotool = shutil.which("xdotool")
    if not xdotool:
        return {"ok": False, "error": "xdotool not found"}

    has_img = latest_png.exists()
    has_md = latest_md.exists() and bool(latest_md.read_text(encoding="utf-8").strip())
    if not has_img and not has_md:
        return {"ok": False, "error": "no latest pinshot artifacts"}

    # Let user's hotkey fingers release before synthesizing Ctrl+V
    time.sleep(0.16)
    subprocess.run(
        [xdotool, "keyup", "Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R", "Shift_L", "Shift_R"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=3,
    )

    pasted_img = False
    pasted_md = False
    if has_img:
        _copy_single_clipboard(latest_png, None, "image")
        time.sleep(0.08)
        subprocess.run([xdotool, "key", "--clearmodifiers", "ctrl+v"], check=True, timeout=3)
        pasted_img = True
        if has_md:
            time.sleep(max(0.15, float(delay_sec)))

    if has_md:
        _copy_single_clipboard(None, latest_md, "prompt")
        time.sleep(0.08)
        subprocess.run([xdotool, "key", "--clearmodifiers", "ctrl+v"], check=True, timeout=3)
        pasted_md = True

    return {"ok": True, "pasted_image": pasted_img, "pasted_markdown": pasted_md}
