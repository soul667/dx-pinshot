"""Favorites collection, batch paste, Markdown bundle, and PDF export for DX PinShot."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
import uuid

from PyQt5 import QtCore, QtGui

from .clipboard import _copy_single_clipboard, get_state_dir
from .tokens import mono_font, serif_font


def _favorites_dir(state_dir: Path | None = None) -> Path:
    root = get_state_dir(state_dir) / "favorites"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _index_path(state_dir: Path | None = None) -> Path:
    return _favorites_dir(state_dir) / "index.json"


def load_favorites(state_dir: Path | None = None) -> list[dict[str, Any]]:
    idx = _index_path(state_dir)
    if not idx.exists():
        return []
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_favorites_index(items: list[dict[str, Any]], state_dir: Path | None = None) -> None:
    idx = _index_path(state_dir)
    tmp = idx.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, idx)


def add_favorite(
    som_image: QtGui.QImage,
    prompt_markdown: str,
    *,
    state_dir: Path | None = None,
    title: str = "",
) -> dict[str, Any]:
    """Persist a Set-of-Mark PNG + Markdown entry into `favorites/` and return its metadata."""
    fav_dir = _favorites_dir(state_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:6]
    stem = f"fav_{stamp}_{uid}"
    png_path = fav_dir / f"{stem}.png"
    md_path = fav_dir / f"{stem}.md"

    som_image.save(str(png_path), "PNG")
    md_text = prompt_markdown.strip()
    md_path.write_text(md_text + "\n", encoding="utf-8")

    first_line = next((ln.strip() for ln in md_text.splitlines() if ln.strip().startswith("- ")), "")
    summary = (title or first_line or f"{som_image.width()}×{som_image.height()}")[:80]
    entry: dict[str, Any] = {
        "id": stem,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "png_path": str(png_path),
        "md_path": str(md_path),
        "width": som_image.width(),
        "height": som_image.height(),
        "summary": summary,
    }
    items = load_favorites(state_dir)
    items.append(entry)
    _save_favorites_index(items, state_dir)
    return entry


def remove_favorite(fav_id: str, state_dir: Path | None = None) -> bool:
    items = load_favorites(state_dir)
    kept: list[dict[str, Any]] = []
    removed = False
    for item in items:
        if item.get("id") == fav_id:
            removed = True
            for key in ("png_path", "md_path"):
                p = Path(str(item.get(key, "")))
                if p.exists():
                    p.unlink(missing_ok=True)
        else:
            kept.append(item)
    if removed:
        _save_favorites_index(kept, state_dir)
    return removed


def clear_favorites(state_dir: Path | None = None) -> int:
    items = load_favorites(state_dir)
    for item in items:
        for key in ("png_path", "md_path"):
            p = Path(str(item.get(key, "")))
            if p.exists():
                p.unlink(missing_ok=True)
    _save_favorites_index([], state_dir)
    return len(items)


def export_favorites_markdown(
    state_dir: Path | None = None,
    out_path: Path | None = None,
    copy_clipboard: bool = True,
) -> Path:
    """Package all favorited captures and instructions into a single Markdown bundle."""
    items = load_favorites(state_dir)
    fav_dir = _favorites_dir(state_dir)
    target = Path(out_path) if out_path is not None else (fav_dir / "favorites_bundle.md")

    lines = [
        "# DX PinShot 收藏夹合集",
        "",
        f"共 {len(items)} 条截图标注 · 导出时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for idx, item in enumerate(items, start=1):
        png_p = Path(str(item.get("png_path", "")))
        md_p = Path(str(item.get("md_path", "")))
        md_body = md_p.read_text(encoding="utf-8").strip() if md_p.exists() else ""
        lines.append(f"## {idx}. {item.get('created_at', item.get('id', ''))}")
        lines.append("")
        if png_p.exists():
            lines.append(f"![PinShot {idx}]({png_p.name})")
            lines.append("")
        if md_body:
            lines.append(md_body)
            lines.append("")

    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    if copy_clipboard and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        _copy_single_clipboard(None, target, "prompt")
    return target


def export_favorites_pdf(
    state_dir: Path | None = None,
    out_path: Path | None = None,
) -> Path:
    """Render all favorited Set-of-Mark PNGs + annotations into a multi-page PDF via Qt QPdfWriter."""
    items = load_favorites(state_dir)
    fav_dir = _favorites_dir(state_dir)
    target = Path(out_path) if out_path is not None else (fav_dir / "favorites_bundle.pdf")

    writer = QtGui.QPdfWriter(str(target))
    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    writer.setResolution(150)
    writer.setTitle("DX PinShot 收藏夹合集")

    painter = QtGui.QPainter(writer)
    page_rect = writer.pageLayout().paintRectPixels(writer.resolution())
    margin = 56

    if not items:
        painter.setFont(serif_font(14, QtGui.QFont.Bold))
        painter.drawText(
            QtCore.QRect(margin, margin, page_rect.width() - 2 * margin, 80),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            "DX PinShot 收藏夹为空",
        )
        painter.end()
        return target

    for idx, item in enumerate(items):
        if idx > 0:
            writer.newPage()

        x0 = margin
        y = margin
        avail_w = page_rect.width() - 2 * margin

        painter.setPen(QtGui.QColor("#191817"))
        painter.setFont(serif_font(13, QtGui.QFont.Bold))
        header_text = f"DX · PinShot 收藏 #{idx + 1}   ({item.get('created_at', '')})"
        painter.drawText(QtCore.QRect(x0, y, avail_w, 42), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, header_text)
        y += 48

        painter.setPen(QtGui.QPen(QtGui.QColor("#DEDBD2"), 2))
        painter.drawLine(x0, y, x0 + avail_w, y)
        y += 20

        png_p = Path(str(item.get("png_path", "")))
        if png_p.exists():
            img = QtGui.QImage(str(png_p))
            if not img.isNull():
                max_img_h = int((page_rect.height() - y - margin) * 0.62)
                scaled = img.scaled(avail_w, max_img_h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                painter.drawImage(x0, y, scaled)
                y += scaled.height() + 24

        md_p = Path(str(item.get("md_path", "")))
        md_body = md_p.read_text(encoding="utf-8").strip() if md_p.exists() else "(无文字批注)"
        painter.setPen(QtGui.QColor("#191817"))
        painter.setFont(mono_font(9.5))
        text_rect = QtCore.QRectF(x0, y, avail_w, max(80, page_rect.height() - y - margin))
        painter.drawText(text_rect, QtCore.Qt.TextWordWrap, md_body)

    painter.end()
    return target


def paste_favorites_bundle(state_dir: Path | None = None, delay_sec: float = 0.35) -> dict[str, Any]:
    """Sequentially paste every favorited PNG + combined Markdown instructions into focused window."""
    items = load_favorites(state_dir)
    if not items:
        return {"ok": False, "error": "favorites is empty"}
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return {"ok": False, "error": "xdotool not found"}

    time.sleep(0.16)
    subprocess.run(
        [xdotool, "keyup", "Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R", "Shift_L", "Shift_R"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=3,
    )

    pasted_images = 0
    for item in items:
        png_p = Path(str(item.get("png_path", "")))
        if png_p.exists():
            _copy_single_clipboard(png_p, None, "image")
            time.sleep(0.08)
            subprocess.run([xdotool, "key", "--clearmodifiers", "ctrl+v"], check=True, timeout=3)
            pasted_images += 1
            time.sleep(max(0.15, float(delay_sec)))

    bundle_md = export_favorites_markdown(state_dir=state_dir, copy_clipboard=False)
    _copy_single_clipboard(None, bundle_md, "prompt")
    time.sleep(0.08)
    subprocess.run([xdotool, "key", "--clearmodifiers", "ctrl+v"], check=True, timeout=3)
    return {"ok": True, "pasted_images": pasted_images, "bundle_md": str(bundle_md)}
