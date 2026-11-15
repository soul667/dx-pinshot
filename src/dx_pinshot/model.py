"""Core annotation state machine, Set-of-Mark rendering, and VLM Markdown export."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import uuid

from PyQt5 import QtCore, QtGui

from .tokens import PAPER_THEME, PinTheme, Theme, get_pin_theme, mono_font


@dataclass
class SubPin:
    """A single spatial target marker inside a thread."""

    x: float
    y: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    label: str = "1"


@dataclass
class PinThread:
    """A comment thread anchored to 1 single pin (`1`) or N grouped sub-pins (`1.1, 1.2...`)."""

    number: int
    pins: list[SubPin] = field(default_factory=list)
    comment: str = ""
    is_grouped: bool = False
    collapsed: bool = False
    committed: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def is_multi_pin(self) -> bool:
        return self.is_grouped or len(self.pins) > 1

    @property
    def labels(self) -> list[str]:
        return [pin.label for pin in self.pins]

    def label_summary(self) -> str:
        return ", ".join(self.labels)


class AnnotationSession:
    """Manages pin placement (`1, 2, 3`), `Alt+Click` grouping (`1.1, 1.2...`), and exports."""

    def __init__(self, crop_width: int = 0, crop_height: int = 0) -> None:
        self.crop_width = max(1, int(crop_width))
        self.crop_height = max(1, int(crop_height))
        self.threads: list[PinThread] = []
        self.active_thread_id: str | None = None
        self._pin_history: list[tuple[str, str]] = []

    def set_crop_size(self, width: int, height: int) -> None:
        self.crop_width = max(1, int(width))
        self.crop_height = max(1, int(height))

    def get_thread(self, thread_id: str | None) -> PinThread | None:
        if not thread_id:
            return None
        for thread in self.threads:
            if thread.id == thread_id:
                return thread
        return None

    @property
    def active_thread(self) -> PinThread | None:
        return self.get_thread(self.active_thread_id)

    @property
    def total_pins(self) -> int:
        return sum(len(thread.pins) for thread in self.threads)

    def renumber(self) -> None:
        """Assign canonical `1, 2, 3` or `N.1, N.2, ...` labels across all threads."""
        for idx, thread in enumerate(self.threads, start=1):
            thread.number = idx
            grouped = thread.is_multi_pin
            for sub_idx, pin in enumerate(thread.pins, start=1):
                pin.label = f"{idx}.{sub_idx}" if grouped else f"{idx}"

    def add_click(self, x: float, y: float, alt_held: bool = False) -> tuple[PinThread, SubPin]:
        """Place a sequential pin (`Click`) or append/start a sub-pin group (`Alt + Click`)."""
        cx = max(0.0, min(float(x), float(self.crop_width)))
        cy = max(0.0, min(float(y), float(self.crop_height)))
        active = self.active_thread

        if alt_held:
            if active is not None:
                active.is_grouped = True
                active.collapsed = False
                pin = SubPin(x=cx, y=cy)
                active.pins.append(pin)
                target_thread = active
            elif self.threads and (not self.threads[-1].is_multi_pin or not self.threads[-1].committed):
                last_thread = self.threads[-1]
                last_thread.is_grouped = True
                last_thread.collapsed = False
                pin = SubPin(x=cx, y=cy)
                last_thread.pins.append(pin)
                self.active_thread_id = last_thread.id
                target_thread = last_thread
            else:
                pin = SubPin(x=cx, y=cy)
                target_thread = PinThread(
                    number=len(self.threads) + 1,
                    pins=[pin],
                    is_grouped=True,
                    collapsed=False,
                )
                self.threads.append(target_thread)
                self.active_thread_id = target_thread.id
        else:
            if active is not None:
                active.collapsed = True
            pin = SubPin(x=cx, y=cy)
            target_thread = PinThread(
                number=len(self.threads) + 1,
                pins=[pin],
                is_grouped=False,
                collapsed=False,
            )
            self.threads.append(target_thread)
            self.active_thread_id = target_thread.id

        self._pin_history.append((target_thread.id, pin.id))
        self.renumber()
        return target_thread, pin

    def set_comment(self, thread_id: str, comment: str) -> None:
        thread = self.get_thread(thread_id)
        if thread is not None:
            thread.comment = comment

    def commit_active_thread(self) -> None:
        active = self.active_thread
        if active is not None:
            active.collapsed = True
            active.committed = True
        self.active_thread_id = None

    def hide_active_bubble(self) -> None:
        """Hide an open bubble without permanently locking an empty pin against Alt+Click promotion."""
        active = self.active_thread
        if active is not None:
            active.collapsed = True
            if active.comment.strip():
                active.committed = True
        self.active_thread_id = None

    def reopen_thread(self, thread_id: str) -> PinThread | None:
        target = self.get_thread(thread_id)
        if target is None:
            return None
        if self.active_thread and self.active_thread.id != thread_id:
            self.active_thread.collapsed = True
        target.collapsed = False
        self.active_thread_id = target.id
        return target

    def delete_thread(self, thread_id: str) -> bool:
        before = len(self.threads)
        self.threads = [t for t in self.threads if t.id != thread_id]
        if len(self.threads) == before:
            return False
        self._pin_history = [(tid, pid) for tid, pid in self._pin_history if tid != thread_id]
        if self.active_thread_id == thread_id:
            self.active_thread_id = None
        self.renumber()
        return True

    def move_pin(self, pin_id: str, x: float, y: float) -> bool:
        """Move a single pin (`1` or `1.2`) to a clamped coordinate within the crop."""
        cx = max(0.0, min(float(x), float(self.crop_width))) if self.crop_width > 0 else float(x)
        cy = max(0.0, min(float(y), float(self.crop_height))) if self.crop_height > 0 else float(y)
        for thread in self.threads:
            for pin in thread.pins:
                if pin.id == pin_id:
                    pin.x = cx
                    pin.y = cy
                    return True
        return False

    def delete_pin(self, pin_id: str) -> bool:
        """Delete a single pin (whether standalone or inside a multi-pin group) and renumber."""
        for thread in list(self.threads):
            orig_len = len(thread.pins)
            thread.pins = [p for p in thread.pins if p.id != pin_id]
            if len(thread.pins) == orig_len:
                continue
            self._pin_history = [(tid, pid) for tid, pid in self._pin_history if pid != pin_id]
            if not thread.pins:
                self.threads = [t for t in self.threads if t.id != thread.id]
                if self.active_thread_id == thread.id:
                    self.active_thread_id = None
            elif len(thread.pins) == 1:
                thread.is_grouped = False
            self.renumber()
            return True
        return False

    def undo_last_pin(self) -> bool:
        while self._pin_history:
            tid, pid = self._pin_history.pop()
            thread = self.get_thread(tid)
            if thread is None:
                continue
            orig_len = len(thread.pins)
            thread.pins = [p for p in thread.pins if p.id != pid]
            if len(thread.pins) == orig_len:
                continue
            if not thread.pins:
                self.threads = [t for t in self.threads if t.id != tid]
                if self.active_thread_id == tid:
                    self.active_thread_id = None
            else:
                if len(thread.pins) == 1 and not any(t_id == tid for t_id, _ in self._pin_history):
                    # Keep grouped flag only if user explicitly created a group
                    pass
                thread.collapsed = False
                self.active_thread_id = tid
            self.renumber()
            return True
        return False

    def clear(self) -> None:
        self.threads.clear()
        self.active_thread_id = None
        self._pin_history.clear()

    def format_vlm_prompt(self) -> str:
        """Render deterministic VLM Markdown instructions keyed 1:1 to Set-of-Mark pin labels."""
        header = f"## DX PinShot Annotations · Set-of-Mark ({self.crop_width}×{self.crop_height})"
        if not self.threads:
            return (
                f"{header}\n\n"
                "_No pin annotations placed yet. Click anywhere inside the crop to add `1, 2, 3...`, "
                "or hold `Alt + Click` to group `1.1, 1.2, 1.3...` under one comment._"
            )

        lines: list[str] = [header, ""]
        for thread in self.threads:
            label_str = ", ".join(thread.labels)
            if len(thread.pins) == 1:
                pin = thread.pins[0]
                coord_str = f"x={round(pin.x)}, y={round(pin.y)}"
            else:
                coord_str = ", ".join(f"({round(p.x)},{round(p.y)})" for p in thread.pins)
            body = " ".join(line.strip() for line in thread.comment.strip().splitlines() if line.strip())
            if not body:
                body = "(no comment)"
            lines.append(f"- **[{label_str}]** ({coord_str}): {body}")
        return "\n".join(lines)


def compute_pin_badge_geometry(
    pin_x: float,
    pin_y: float,
    label: str,
    bounds_w: float,
    bounds_h: float,
    scale: float = 1.0,
) -> tuple[QtCore.QPointF, QtCore.QRectF, QtCore.QPointF]:
    """Compute `(target_pt, badge_rect, stem_end)` inside `(bounds_w, bounds_h)` with 0px sharp proportions."""
    font = mono_font(11.5 * scale, QtGui.QFont.Bold)
    metrics = QtGui.QFontMetricsF(font)
    text_w = metrics.horizontalAdvance(label)
    pad_x = 8.0 * scale
    badge_w = max(28.0 * scale, math.ceil(text_w + pad_x * 2.0))
    badge_h = 25.0 * scale
    gap = 7.0 * scale

    px = max(0.0, min(float(pin_x), float(bounds_w)))
    py = max(0.0, min(float(pin_y), float(bounds_h)))

    # Default quadrant: above-right of the target dot so the dot itself stays uncovered
    bx = px + gap
    by = py - badge_h - gap

    if bx + badge_w > bounds_w - 2.0:
        bx = px - badge_w - gap
    if bx < 2.0:
        bx = 2.0

    if by < 2.0:
        by = py + gap
    if by + badge_h > bounds_h - 2.0:
        by = max(2.0, bounds_h - badge_h - 2.0)

    rect = QtCore.QRectF(bx, by, badge_w, badge_h)
    stem_x = max(rect.left(), min(px, rect.right()))
    stem_y = max(rect.top(), min(py, rect.bottom()))
    return QtCore.QPointF(px, py), rect, QtCore.QPointF(stem_x, stem_y)


def paint_som_pins(
    painter: QtGui.QPainter,
    session: AnnotationSession,
    theme: Theme,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    highlight_active: bool = True,
    pin_scale: float = 1.0,
    pin_theme: str | PinTheme = "translucent-ink",
) -> dict[str, tuple[str, QtCore.QRectF]]:
    """Paint all Set-of-Mark markers (`1`, `2.1`, `2.2`...) onto `painter`; returns `{pin_id: (thread_id, rect)}`."""
    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

    pt = pin_theme if isinstance(pin_theme, PinTheme) else get_pin_theme(pin_theme, ui_theme=theme)
    fill_color = QtGui.QColor(*pt.fill_rgba)
    halo_color = QtGui.QColor(*pt.halo_rgba)

    base_scale = max(0.5, (scale_x + scale_y) * 0.5)
    scale = max(0.65, base_scale * max(0.75, min(1.5, float(pin_scale))))
    font = mono_font(11.5 * scale, QtGui.QFont.Bold)
    painter.setFont(font)

    bounds_w = session.crop_width * scale_x
    bounds_h = session.crop_height * scale_y
    hit_map: dict[str, tuple[str, QtCore.QRectF]] = {}

    for thread in session.threads:
        is_active = highlight_active and (thread.id == session.active_thread_id)
        for pin in thread.pins:
            local_px = pin.x * scale_x
            local_py = pin.y * scale_y
            target_local, rect_local, stem_local = compute_pin_badge_geometry(
                local_px, local_py, pin.label, bounds_w, bounds_h, scale=scale
            )
            target = QtCore.QPointF(offset_x + target_local.x(), offset_y + target_local.y())
            rect = rect_local.translated(offset_x, offset_y)
            stem = QtCore.QPointF(offset_x + stem_local.x(), offset_y + stem_local.y())

            # Crisp directional arrow from badge stem pointing to target coordinate
            halo_pen = QtGui.QPen(halo_color, 2.6 * scale)
            painter.setPen(halo_pen)
            painter.drawLine(target, stem)

            stem_color = QtGui.QColor(pt.active_hex) if is_active else fill_color
            painter.setPen(QtGui.QPen(stem_color, 1.5 * scale))
            painter.drawLine(target, stem)

            dx = target.x() - stem.x()
            dy = target.y() - stem.y()
            dist = math.hypot(dx, dy)
            if dist >= 1.5:
                ux, uy = dx / dist, dy / dist
                px_n, py_n = -uy, ux
                alen = 6.5 * scale
                awid = 3.2 * scale
                tip = target
                base_l = QtCore.QPointF(tip.x() - ux * alen + px_n * awid, tip.y() - uy * alen + py_n * awid)
                base_r = QtCore.QPointF(tip.x() - ux * alen - px_n * awid, tip.y() - uy * alen - py_n * awid)
                arrow_poly = QtGui.QPolygonF([tip, base_l, base_r])
                painter.setPen(QtGui.QPen(halo_color, 1.2 * scale))
                painter.setBrush(stem_color)
                painter.drawPolygon(arrow_poly)
            else:
                dot_r = 3.8 * scale
                painter.setPen(QtGui.QPen(halo_color, 1.4 * scale))
                painter.setBrush(stem_color)
                painter.drawRect(QtCore.QRectF(target.x() - dot_r, target.y() - dot_r, dot_r * 2.0, dot_r * 2.0))

            # Outer subtle halo so the sharp 0px badge pops equally well on white and dark apps
            painter.setPen(QtGui.QPen(halo_color, 2.2 * scale))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRect(rect)

            # Primary sharp rectangular pin badge (0px radius, translucent fill)
            border_color = pt.active_hex if (is_active or thread.is_multi_pin) else pt.border_hex
            painter.setPen(QtGui.QPen(QtGui.QColor(border_color), 1.3 * scale))
            painter.setBrush(fill_color)
            painter.drawRect(rect)

            # High-contrast monospace pin number (`1`, `2.1`, `2.2`...)
            painter.setPen(QtGui.QColor(pt.text_hex))
            painter.drawText(rect, QtCore.Qt.AlignCenter, pin.label)

            hit_rect = rect.adjusted(-4.0, -4.0, 4.0, 4.0).united(
                QtCore.QRectF(target.x() - 7.0, target.y() - 7.0, 14.0, 14.0)
            )
            hit_map[pin.id] = (thread.id, hit_rect)

    painter.restore()
    return hit_map


def render_som_image(
    cropped_image: QtGui.QImage,
    session: AnnotationSession,
    theme: Theme = PAPER_THEME,
    highlight_active: bool = False,
    pin_scale: float = 1.0,
    pin_theme: str | PinTheme = "translucent-ink",
) -> QtGui.QImage:
    """Return a clean Set-of-Mark QImage with ONLY sharp numbered markers baked onto `cropped_image`."""
    out = cropped_image.convertToFormat(QtGui.QImage.Format_ARGB32).copy()
    if out.isNull():
        return out

    sx = out.width() / float(max(1, session.crop_width))
    sy = out.height() / float(max(1, session.crop_height))

    painter = QtGui.QPainter(out)
    paint_som_pins(
        painter,
        session,
        theme=theme,
        offset_x=0.0,
        offset_y=0.0,
        scale_x=sx,
        scale_y=sy,
        highlight_active=highlight_active,
        pin_scale=pin_scale,
        pin_theme=pin_theme,
    )
    painter.end()
    return out
