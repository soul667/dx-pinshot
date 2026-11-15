"""DX PinShot UI: Phase 1 fullscreen crop selector -> Phase 2 independent floating PinShotPopup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt5 import QtCore, QtGui, QtWidgets

from .clipboard import ExportResult, export_html_preview, export_session
from .config import load_config, modifier_display_label, normalize_config, resolve_theme_name
from .model import AnnotationSession, PinThread, paint_som_pins, render_som_image
from .tokens import (
    MONO_CSS,
    SERIF_CSS,
    Theme,
    build_widget_qss,
    get_theme,
    mono_font,
    serif_font,
)
from .x11_keys import is_hardware_modifier_held


class SlipCommentEdit(QtWidgets.QPlainTextEdit):
    """Borderless serif comment input: Enter commits & hides bubble, Shift+Enter newline, Ctrl+Enter export."""

    commitRequested = QtCore.pyqtSignal()
    exportRequested = QtCore.pyqtSignal()
    cancelRequested = QtCore.pyqtSignal()
    textChangedDebounced = QtCore.pyqtSignal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameStyle(QtWidgets.QFrame.NoFrame)
        self.setTabChangesFocus(False)
        self.setPlaceholderText("输入批注…")
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.textChanged.connect(lambda: self.textChangedDebounced.emit(self.toPlainText()))

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if mods & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier):
                self.exportRequested.emit()
                event.accept()
                return
            if not (mods & QtCore.Qt.ShiftModifier):
                self.commitRequested.emit()
                event.accept()
                return
        elif key == QtCore.Qt.Key_Escape:
            self.cancelRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _style_multi_toggle_btn(
    btn: QtWidgets.QPushButton,
    theme: Theme,
    active: bool,
    *,
    compact: bool = False,
) -> None:
    """Apply crisp 0px DX Paper/Obsidian styling to a multi-select toggle button."""
    pad = "1px 6px" if compact else "2px 7px"
    font_size = "10px" if compact else "10.5px"
    if active:
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {theme.anchor_line};
                color: #FFFFFF;
                border: 1px solid {theme.anchor_line};
                border-radius: 0px;
                padding: {pad};
                font-family: {MONO_CSS};
                font-size: {font_size};
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {theme.text_primary};
                border-color: {theme.text_primary};
                color: {theme.bg_page};
            }}
            """
        )
    else:
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {theme.text_secondary};
                border: 1px solid {theme.border_hairline};
                border-radius: 0px;
                padding: {pad};
                font-family: {MONO_CSS};
                font-size: {font_size};
            }}
            QPushButton:hover {{
                border-color: {theme.anchor_line};
                color: {theme.text_primary};
            }}
            """
        )


class PinCommentBubble(QtWidgets.QFrame):
    """Ultra-minimal inline popover showing only pin badge (`[1]` / `[2.1, 2.2...]`) + borderless input."""

    commitClicked = QtCore.pyqtSignal()
    deleteClicked = QtCore.pyqtSignal(str)
    exportClicked = QtCore.pyqtSignal()
    commentEdited = QtCore.pyqtSignal(str, str)
    multiSelectToggled = QtCore.pyqtSignal(bool)

    def __init__(
        self,
        theme: Theme,
        modifier_label: str = "Alt",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._modifier_label = modifier_label
        self._thread_id: str | None = None
        self._updating_text = False
        self._multi_visual_active = False
        self.setFixedWidth(308)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 6)
        shadow.setColor(QtGui.QColor(25, 24, 23, 40))
        self.setGraphicsEffect(shadow)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Compact single header row
        self.header = QtWidgets.QFrame(self)
        self.header.setFixedHeight(26)
        h_layout = QtWidgets.QHBoxLayout(self.header)
        h_layout.setContentsMargins(8, 0, 6, 0)
        h_layout.setSpacing(6)

        self.badge_label = QtWidgets.QLabel("[1]", self.header)
        self.badge_label.setFont(mono_font(8.4, QtGui.QFont.Bold))
        h_layout.addWidget(self.badge_label, 1)

        self.multi_btn = QtWidgets.QPushButton(f"+ 多选加点 ({self._modifier_label})", self.header)
        self.multi_btn.setCheckable(True)
        self.multi_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.multi_btn.setToolTip(f"点击开启多选加点：可直接左键连续添加 1.1, 1.2, 1.3…（等效按住 {self._modifier_label}）")
        self.multi_btn.setFont(mono_font(7.4))
        self.multi_btn.toggled.connect(self.multiSelectToggled.emit)
        h_layout.addWidget(self.multi_btn)
        self.hint_label = self.multi_btn

        self.delete_btn = QtWidgets.QPushButton("×", self.header)
        self.delete_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.delete_btn.setToolTip("删除此条标记")
        self.delete_btn.setFixedSize(18, 18)
        self.delete_btn.clicked.connect(self._on_delete)
        h_layout.addWidget(self.delete_btn)
        layout.addWidget(self.header)

        # Borderless serif comment input
        self.editor = SlipCommentEdit(self)
        self.editor.setFont(serif_font(10.0))
        self.editor.setFixedHeight(55)
        self.editor.commitRequested.connect(self.commitClicked.emit)
        self.editor.exportRequested.connect(self.exportClicked.emit)
        self.editor.cancelRequested.connect(self.commitClicked.emit)
        self.editor.textChangedDebounced.connect(self._on_text_changed)
        layout.addWidget(self.editor)

        self.apply_theme(theme)
        self.adjustSize()

    def _on_text_changed(self, text: str) -> None:
        if self._updating_text or not self._thread_id:
            return
        self.commentEdited.emit(self._thread_id, text)

    def _on_delete(self) -> None:
        if self._thread_id:
            self.deleteClicked.emit(self._thread_id)

    def set_modifier_label(self, modifier_label: str) -> None:
        self._modifier_label = modifier_label
        self._refresh_multi_btn_text()

    def _refresh_multi_btn_text(self) -> None:
        if self._multi_visual_active:
            self.multi_btn.setText("● 继续加点中")
        else:
            self.multi_btn.setText(f"+ 多选加点 ({self._modifier_label})")

    def set_multi_state(self, *, visual_active: bool, checked: bool) -> None:
        self._multi_visual_active = bool(visual_active)
        was_blocked = self.multi_btn.blockSignals(True)
        self.multi_btn.setChecked(bool(checked))
        self.multi_btn.blockSignals(was_blocked)
        self._refresh_multi_btn_text()
        _style_multi_toggle_btn(self.multi_btn, self._theme, self._multi_visual_active, compact=True)

    def set_thread(self, thread: PinThread) -> None:
        self._thread_id = thread.id
        badge_str = f"[{', '.join(thread.labels)}]"
        self.badge_label.setText(badge_str)
        self._refresh_multi_btn_text()

        needed_w = 308 if len(badge_str) < 14 else min(360, 196 + len(badge_str) * 6)
        self.setFixedWidth(needed_w)

        self._updating_text = True
        self.editor.setPlainText(thread.comment)
        cursor = self.editor.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.editor.setTextCursor(cursor)
        self._updating_text = False
        self.adjustSize()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"""
            PinCommentBubble {{
                background-color: {theme.bg_panel};
                border: 1px solid {theme.border_strong};
                border-left: 2px solid {theme.anchor_line};
                border-radius: 0px;
            }}
            """
        )
        self.header.setStyleSheet(
            f"""
            QFrame {{
                background-color: {theme.bg_page};
                border: none;
                border-bottom: 1px solid {theme.border_hairline};
            }}
            """
        )
        self.badge_label.setStyleSheet(f"color: {theme.text_primary}; border: none; background: transparent;")
        _style_multi_toggle_btn(self.multi_btn, theme, self._multi_visual_active, compact=True)
        self.delete_btn.setStyleSheet(
            f"""
            QPushButton {{
                border: none; background: transparent; color: {theme.text_muted};
                font-family: {MONO_CSS}; font-size: 12.5px; padding: 0px;
            }}
            QPushButton:hover {{ color: {theme.redline_del}; }}
            """
        )
        self.editor.setStyleSheet(
            f"""
            QPlainTextEdit {{
                background-color: {theme.bg_panel};
                color: {theme.text_primary};
                border: none;
                padding: 6px 8px;
                font-family: {SERIF_CSS};
                font-size: 13px;
            }}
            """
        )


class PopupImageCanvas(QtWidgets.QWidget):
    """Centered image stage inside PinShotPopup rendering Set-of-Mark pins and active leader lines."""

    pinClicked = QtCore.pyqtSignal(float, float, bool)
    existingPinClicked = QtCore.pyqtSignal(str)
    pinMoved = QtCore.pyqtSignal(str)
    pinDeleted = QtCore.pyqtSignal(str)
    backgroundCommitRequested = QtCore.pyqtSignal()

    def __init__(
        self,
        cropped_pixmap: QtGui.QPixmap,
        disp_size: QtCore.QSize,
        pad: int,
        session: AnnotationSession,
        theme: Theme,
        bubble: PinCommentBubble,
        multi_select_checker=None,
        pin_scale: float = 1.0,
        show_leader_lines: bool = True,
        pin_theme: str = "translucent-ink",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pixmap = cropped_pixmap
        self._disp_size = disp_size
        self._pad = pad
        self._session = session
        self._theme = theme
        self._bubble = bubble
        self._multi_select_checker = multi_select_checker
        self._pin_scale = float(pin_scale)
        self._show_leader_lines = bool(show_leader_lines)
        self._pin_theme = pin_theme
        self._pin_hit_map: dict[str, tuple[str, QtCore.QRectF]] = {}
        self._dragging_pin_id: str | None = None
        self._dragging_thread_id: str | None = None
        self._drag_start_pos: QtCore.QPoint | None = None
        self._drag_moved = False
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CrossCursor)

    @property
    def scale_x(self) -> float:
        return self._disp_size.width() / float(max(1, self._session.crop_width))

    @property
    def scale_y(self) -> float:
        return self._disp_size.height() / float(max(1, self._session.crop_height))

    def image_rect(self) -> QtCore.QRect:
        ix = max(self._pad, (self.width() - self._disp_size.width()) // 2)
        iy = max(self._pad, (self.height() - self._disp_size.height()) // 2)
        return QtCore.QRect(ix, iy, self._disp_size.width(), self._disp_size.height())

    def crop_to_canvas(self, crop_x: float, crop_y: float) -> QtCore.QPointF:
        rect = self.image_rect()
        return QtCore.QPointF(rect.left() + crop_x * self.scale_x, rect.top() + crop_y * self.scale_y)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def set_display_size(self, disp_size: QtCore.QSize, canvas_size: QtCore.QSize) -> None:
        self._disp_size = disp_size
        self.setFixedSize(canvas_size)
        self.update()

    def _hit_pin(self, pos: QtCore.QPoint) -> tuple[str, str] | None:
        pt = QtCore.QPointF(pos)
        for pid, (thread_id, hit_rect) in reversed(list(self._pin_hit_map.items())):
            if hit_rect.contains(pt):
                return pid, thread_id
        return None

    def handle_canvas_click(self, pos: QtCore.QPoint, multi_select: bool) -> None:
        """Process a canvas click or bubble click-through at canvas-local coordinates."""
        rect = self.image_rect()
        if not multi_select:
            hit = self._hit_pin(pos)
            if hit is not None:
                self.existingPinClicked.emit(hit[1])
                return

        if rect.contains(pos):
            crop_x = (pos.x() - rect.left()) / self.scale_x
            crop_y = (pos.y() - rect.top()) / self.scale_y
            self.pinClicked.emit(crop_x, crop_y, multi_select)
        else:
            self.backgroundCommitRequested.emit()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.RightButton:
            hit = self._hit_pin(event.pos())
            if hit is not None:
                self.pinDeleted.emit(hit[0])
            else:
                self.backgroundCommitRequested.emit()
            event.accept()
            return
        if event.button() != QtCore.Qt.LeftButton:
            return

        mods = event.modifiers() | QtGui.QGuiApplication.queryKeyboardModifiers()
        multi_held = (
            self._multi_select_checker(mods)
            if self._multi_select_checker is not None
            else bool(mods & QtCore.Qt.AltModifier)
        )
        if not multi_held:
            hit = self._hit_pin(event.pos())
            if hit is not None:
                self._dragging_pin_id, self._dragging_thread_id = hit
                self._drag_start_pos = event.pos()
                self._drag_moved = False
                event.accept()
                return
        self.handle_canvas_click(event.pos(), multi_select=multi_held)
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self._dragging_pin_id is not None:
            tid = self._dragging_thread_id
            moved = self._drag_moved
            self._dragging_pin_id = None
            self._dragging_thread_id = None
            self._drag_start_pos = None
            self._drag_moved = False
            self.setCursor(QtCore.Qt.OpenHandCursor)
            if not moved and tid is not None:
                self.existingPinClicked.emit(tid)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        # Rapid multi-select clicks (Alt+Click, Alt+Click) arrive as QMouseEvent DblClick in Qt
        if event.button() == QtCore.Qt.LeftButton:
            self.mousePressEvent(event)
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging_pin_id is not None and self._drag_start_pos is not None:
            if not self._drag_moved and (event.pos() - self._drag_start_pos).manhattanLength() >= 4:
                self._drag_moved = True
            if self._drag_moved:
                rect = self.image_rect()
                crop_x = (event.pos().x() - rect.left()) / self.scale_x
                crop_y = (event.pos().y() - rect.top()) / self.scale_y
                if self._session.move_pin(self._dragging_pin_id, crop_x, crop_y):
                    self.setCursor(QtCore.Qt.ClosedHandCursor)
                    self.update()
                    if self._dragging_thread_id is not None:
                        self.pinMoved.emit(self._dragging_thread_id)
                event.accept()
                return

        pos = QtCore.QPointF(event.pos())
        hovered_tip = ""
        is_pin = False
        for _pid, (thread_id, hit_rect) in reversed(list(self._pin_hit_map.items())):
            if hit_rect.contains(pos):
                is_pin = True
                thread = self._session.get_thread(thread_id)
                if thread is not None:
                    comment = " ".join(thread.comment.strip().split()) or "(未填写批注，点击编辑)"
                    hovered_tip = f"[{', '.join(thread.labels)}] {comment}  ·  左键拖动改位置 · 右键单独删除"
                break
        self.setCursor(QtCore.Qt.OpenHandCursor if is_pin else QtCore.Qt.CrossCursor)
        if hovered_tip:
            QtWidgets.QToolTip.showText(event.globalPos(), hovered_tip, self)
        else:
            QtWidgets.QToolTip.hideText()

    def _draw_active_leader_curves(self, painter: QtGui.QPainter) -> None:
        active = self._session.active_thread
        if active is None or self._bubble.isHidden() or not active.pins:
            return

        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pen = QtGui.QPen(QtGui.QColor(self._theme.anchor_line), 1.5, QtCore.Qt.DashLine)
        pen.setDashPattern([4.5, 3.0])

        # Convert bubble geometry from parent (PinShotPopup) coordinates into PopupImageCanvas coordinates
        top_left = self.mapFromParent(self._bubble.geometry().topLeft())
        b_rect = QtCore.QRect(top_left, self._bubble.size())

        n_pins = len(active.pins)
        for idx, pin in enumerate(active.pins):
            pt = self.crop_to_canvas(pin.x, pin.y)
            px, py = pt.x(), pt.y()

            if b_rect.top() >= py + 12:
                spread = (idx - (n_pins - 1) / 2.0) * 26.0
                sx = max(float(b_rect.left() + 18), min(float(b_rect.center().x()) + spread, float(b_rect.right() - 18)))
                sy = float(b_rect.top())
                mid_y = (py + sy) * 0.5
                c1, c2 = QtCore.QPointF(px, mid_y), QtCore.QPointF(sx, mid_y)
            elif b_rect.bottom() <= py - 12:
                spread = (idx - (n_pins - 1) / 2.0) * 26.0
                sx = max(float(b_rect.left() + 18), min(float(b_rect.center().x()) + spread, float(b_rect.right() - 18)))
                sy = float(b_rect.bottom())
                mid_y = (py + sy) * 0.5
                c1, c2 = QtCore.QPointF(px, mid_y), QtCore.QPointF(sx, mid_y)
            else:
                sx = float(b_rect.left()) if b_rect.left() >= px else float(b_rect.right())
                spread = (idx - (n_pins - 1) / 2.0) * 10.0
                sy = max(float(b_rect.top() + 12), min(float(b_rect.top()) + 26.0 + spread, float(b_rect.bottom() - 12)))
                ctrl_x = (px + sx) * 0.5
                c1, c2 = QtCore.QPointF(ctrl_x, py), QtCore.QPointF(ctrl_x, sy)

            path = QtGui.QPainterPath(QtCore.QPointF(px, py))
            path.cubicTo(c1, c2, QtCore.QPointF(sx, sy))
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawPath(path)

            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(self._theme.anchor_line))
            painter.drawEllipse(QtCore.QPointF(sx, sy), 2.4, 2.4)

        painter.restore()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(self._theme.bg_subtle))

        img_rect = self.image_rect()
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(img_rect, self._pixmap)

        # Crisp 1px DX hairline framing the captured screenshot
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        painter.setPen(QtGui.QPen(QtGui.QColor(self._theme.border_hairline), 1))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(img_rect.adjusted(-1, -1, 0, 0))

        if self._show_leader_lines:
            self._draw_active_leader_curves(painter)

        self._pin_hit_map = paint_som_pins(
            painter,
            self._session,
            theme=self._theme,
            offset_x=float(img_rect.left()),
            offset_y=float(img_rect.top()),
            scale_x=self.scale_x,
            scale_y=self.scale_y,
            highlight_active=True,
            pin_scale=self._pin_scale,
            pin_theme=self._pin_theme,
        )
        painter.end()


class PinShotPopup(QtWidgets.QWidget):
    """Independent minimalist DX Writing floating popup for pin-commenting a captured screenshot."""

    MAX_CANVAS_W = 920
    MAX_CANVAS_H = 620
    CANVAS_PAD = 16
    MIN_WIN_W = 560
    HEADER_H = 34
    FOOTER_H = 26

    def __init__(
        self,
        cropped_pixmap: QtGui.QPixmap | None = None,
        *,
        backdrop: QtGui.QPixmap | None = None,
        initial_crop: QtCore.QRect | None = None,
        theme_name: str | None = None,
        state_dir: Path | None = None,
        copy_clipboard: bool = True,
        config: dict[str, Any] | None = None,
        config_dir: Path | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if cropped_pixmap is None:
            if backdrop is None:
                raise ValueError("Provide either `cropped_pixmap` or `backdrop` + `initial_crop`.")
            dpr = max(1.0, float(backdrop.devicePixelRatio()))
            crop = initial_crop if initial_crop is not None else QtCore.QRect(0, 0, round(backdrop.width() / dpr), round(backdrop.height() / dpr))
            scaled = QtCore.QRect(
                round(crop.x() * dpr),
                round(crop.y() * dpr),
                round(crop.width() * dpr),
                round(crop.height() * dpr),
            ).intersected(backdrop.rect())
            cropped_pixmap = backdrop.copy(scaled)

        self._cropped_pixmap = cropped_pixmap
        self._dpr = max(1.0, float(cropped_pixmap.devicePixelRatio()))
        crop_w = max(1, round(cropped_pixmap.width() / self._dpr))
        crop_h = max(1, round(cropped_pixmap.height() / self._dpr))

        if crop_w <= self.MAX_CANVAS_W and crop_h <= self.MAX_CANVAS_H:
            disp_w, disp_h = crop_w, crop_h
        else:
            ratio = min(self.MAX_CANVAS_W / float(crop_w), self.MAX_CANVAS_H / float(crop_h))
            disp_w = max(1, round(crop_w * ratio))
            disp_h = max(1, round(crop_h * ratio))

        self.config = normalize_config(config) if config is not None else load_config(config_dir)
        effective_theme = resolve_theme_name(self.config["theme"] if theme_name is None else theme_name)
        self.theme: Theme = get_theme(effective_theme)
        self.state_dir = state_dir
        self.copy_clipboard = copy_clipboard
        self._multi_mod_name = str(self.config["multi_select_modifier"])
        self._multi_mod_label = modifier_display_label(self._multi_mod_name)
        self._export_mode = str(self.config["export_mode"])
        self._pin_scale = float(self.config["pin_scale"])
        self._pin_theme = str(self.config.get("pin_theme", "translucent-ink"))
        self._show_leader_lines = bool(self.config["show_leader_lines"])
        self._keep_history_count = int(self.config["keep_history_count"])
        self._tracked_mod_down = False
        self._sticky_multi_select = False
        self._last_multi_active: bool | None = None
        self.session = AnnotationSession(crop_w, crop_h)
        self._drag_offset: QtCore.QPoint | None = None
        self._toast_text = ""

        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Dialog
        )
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        win_w = max(self.MIN_WIN_W, disp_w + 2 * self.CANVAS_PAD)
        stage_h = max(disp_h + 2 * self.CANVAS_PAD, min(self.MAX_CANVAS_H, disp_h + 184))
        win_h = self.HEADER_H + stage_h + self.FOOTER_H
        self._normal_disp_size = QtCore.QSize(disp_w, disp_h)
        self._normal_canvas_size = QtCore.QSize(win_w, stage_h)
        self._normal_win_size = QtCore.QSize(win_w + 2, win_h + 2)
        self._is_fullscreen = False
        self._normal_pos: QtCore.QPoint | None = None
        self.setFixedSize(self._normal_win_size)  # +2 for crisp 1px outer frame border

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        # Slim draggable header bar
        self.header = QtWidgets.QFrame(self)
        self.header.setFixedHeight(self.HEADER_H)
        h_layout = QtWidgets.QHBoxLayout(self.header)
        h_layout.setContentsMargins(12, 0, 8, 0)
        h_layout.setSpacing(6)

        self.brand_lbl = QtWidgets.QLabel("DX · PinShot", self.header)
        self.brand_lbl.setFont(serif_font(10.2, QtGui.QFont.Bold))
        h_layout.addWidget(self.brand_lbl)

        self.meta_lbl = QtWidgets.QLabel(f"{crop_w}×{crop_h} · 0 处标注", self.header)
        self.meta_lbl.setFont(mono_font(8.0))
        h_layout.addWidget(self.meta_lbl)
        h_layout.addStretch(1)

        self.multi_mode_btn = QtWidgets.QPushButton(f"多选 ({self._multi_mod_label})", self.header)
        self.multi_mode_btn.setCheckable(True)
        self.multi_mode_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.multi_mode_btn.setToolTip(
            f"开启/关闭连续多选模式（也可直接按住 {self._multi_mod_label} 点击画面添加 1.1, 1.2, 1.3…）"
        )
        self.multi_mode_btn.toggled.connect(self._set_sticky_multi_select)
        h_layout.addWidget(self.multi_mode_btn)

        self.undo_btn = self._make_action_btn("撤回", self.undo_last_pin, "撤回上一个点 (Ctrl+Z)")
        self.clear_btn = self._make_action_btn("清空", self.clear_all, "清空所有标注")
        self.copy_img_btn = self._make_action_btn("复制图片", lambda: self._export("image", close_after=False), "仅复制 Set-of-Mark 图片到剪贴板")
        self.copy_md_btn = self._make_action_btn("复制指令", lambda: self._export("markdown", close_after=False), "仅复制 Markdown Prompt")
        self.open_web_btn = self._make_action_btn("网页打开", self.open_in_browser, "在外部浏览器中打开交互预览页 (Ctrl+O)")
        self.fullscreen_btn = self._make_action_btn("全屏", self.toggle_fullscreen, "切换全屏/还原视图 (F11)")
        self._dots: list[QtWidgets.QLabel] = []
        for idx, btn in enumerate((self.undo_btn, self.clear_btn, self.copy_img_btn, self.copy_md_btn, self.open_web_btn, self.fullscreen_btn)):
            dot = QtWidgets.QLabel("·", self.header)
            dot.setFont(mono_font(8.8))
            h_layout.addWidget(dot)
            self._dots.append(dot)
            h_layout.addWidget(btn)

        self.export_btn = QtWidgets.QPushButton("完成并复制 ↵", self.header)
        self.export_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.export_btn.setToolTip("导出 Set-of-Mark 图片 + Markdown 并退出 (Ctrl+Enter)")
        self.export_btn.clicked.connect(lambda: self._export(self._export_mode, close_after=True))
        h_layout.addWidget(self.export_btn)

        self.close_btn = QtWidgets.QPushButton("×", self.header)
        self.close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.close_btn.setToolTip("取消退出 (Esc)")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.clicked.connect(self.close)
        h_layout.addWidget(self.close_btn)
        root.addWidget(self.header)

        # Floating comment bubble overlays popup directly (not clipped to canvas alone)
        self.bubble = PinCommentBubble(self.theme, modifier_label=self._multi_mod_label, parent=self)
        self.bubble.commitClicked.connect(self.commit_active_thread)
        self.bubble.editor.cancelRequested.disconnect()
        self.bubble.editor.cancelRequested.connect(self.dismiss_active_bubble)
        self.bubble.deleteClicked.connect(self._on_delete_thread)
        self.bubble.exportClicked.connect(lambda: self._export(self._export_mode, close_after=True))
        self.bubble.commentEdited.connect(self._on_comment_edited)
        self.bubble.multiSelectToggled.connect(self._set_sticky_multi_select)
        self.bubble.hide()

        # Image canvas stage
        self.canvas = PopupImageCanvas(
            cropped_pixmap=cropped_pixmap,
            disp_size=QtCore.QSize(disp_w, disp_h),
            pad=self.CANVAS_PAD,
            session=self.session,
            theme=self.theme,
            bubble=self.bubble,
            multi_select_checker=self.is_multi_select_active,
            pin_scale=self._pin_scale,
            show_leader_lines=self._show_leader_lines,
            pin_theme=self._pin_theme,
            parent=self,
        )
        self.canvas.setFixedSize(win_w, stage_h)
        self.canvas.pinClicked.connect(self._on_canvas_pin_clicked)
        self.canvas.existingPinClicked.connect(self._on_existing_pin_clicked)
        self.canvas.pinMoved.connect(self._on_pin_moved)
        self.canvas.pinDeleted.connect(self._on_delete_pin)
        self.canvas.backgroundCommitRequested.connect(self.dismiss_active_bubble)
        root.addWidget(self.canvas)

        # Slim footer bar
        self.footer = QtWidgets.QFrame(self)
        self.footer.setFixedHeight(self.FOOTER_H)
        f_layout = QtWidgets.QHBoxLayout(self.footer)
        f_layout.setContentsMargins(12, 0, 12, 0)
        self.footer_hint = QtWidgets.QLabel(self._default_footer_hint(), self.footer)
        self.footer_hint.setFont(mono_font(7.6))
        f_layout.addWidget(self.footer_hint, 1)

        self.toast_lbl = QtWidgets.QLabel("", self.footer)
        self.toast_lbl.setFont(mono_font(7.6, QtGui.QFont.Bold))
        f_layout.addWidget(self.toast_lbl)
        root.addWidget(self.footer)

        self._bubble_click_targets = (
            self.bubble,
            self.bubble.header,
            self.bubble.badge_label,
            self.bubble.editor,
            self.bubble.editor.viewport(),
        )
        for watched in (self, self.header, self.canvas, *self._bubble_click_targets):
            watched.installEventFilter(self)

        self._mod_poll_timer = QtCore.QTimer(self)
        self._mod_poll_timer.setInterval(35)
        self._mod_poll_timer.timeout.connect(self._poll_multi_modifier)

        self.apply_theme(self.theme)
        self._update_multi_ui_state()
        self.sync_ui(focus_editor=False)

    # Compatibility alias with previous tests
    @property
    def slip(self) -> PinCommentBubble:
        return self.bubble

    def _default_footer_hint(self) -> str:
        return (
            f"Click 打点 · {self._multi_mod_label}+Click/「多选」连点 (1.1, 1.2…) · "
            "拖动标点改位置 · 右键单删标点 · Ctrl+Enter 完成复制"
        )

    def _make_action_btn(self, text: str, slot, tooltip: str = "") -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(text, self.header)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.clicked.connect(slot)
        return btn

    def _target_modifier_spec(self) -> tuple[QtCore.Qt.KeyboardModifiers, set[int]]:
        if self._multi_mod_name == "shift":
            return QtCore.Qt.ShiftModifier, {QtCore.Qt.Key_Shift}
        if self._multi_mod_name == "ctrl":
            return QtCore.Qt.ControlModifier, {QtCore.Qt.Key_Control}
        return (
            QtCore.Qt.AltModifier | QtCore.Qt.MetaModifier | QtCore.Qt.GroupSwitchModifier,
            {
                QtCore.Qt.Key_Alt,
                QtCore.Qt.Key_AltGr,
                QtCore.Qt.Key_Meta,
                QtCore.Qt.Key_Super_L,
                QtCore.Qt.Key_Super_R,
            },
        )

    def is_modifier_key_held(
        self, event_modifiers: QtCore.Qt.KeyboardModifiers | int = QtCore.Qt.NoModifier
    ) -> bool:
        mod_flag, _keys = self._target_modifier_spec()
        combined = QtCore.Qt.KeyboardModifiers(event_modifiers) | QtGui.QGuiApplication.queryKeyboardModifiers()
        return bool(
            bool(combined & mod_flag)
            or self._tracked_mod_down
            or is_hardware_modifier_held(self._multi_mod_name)
        )

    def is_multi_select_active(
        self, event_modifiers: QtCore.Qt.KeyboardModifiers | int = QtCore.Qt.NoModifier
    ) -> bool:
        return bool(self._sticky_multi_select or self.is_modifier_key_held(event_modifiers))

    def _set_sticky_multi_select(self, enabled: bool) -> None:
        self._sticky_multi_select = bool(enabled)
        self._update_multi_ui_state()

    def _update_multi_ui_state(self) -> None:
        active = bool(self._sticky_multi_select or is_hardware_modifier_held(self._multi_mod_name) or self._tracked_mod_down)
        self._last_multi_active = active

        was_blocked = self.multi_mode_btn.blockSignals(True)
        self.multi_mode_btn.setChecked(self._sticky_multi_select)
        self.multi_mode_btn.blockSignals(was_blocked)
        self.multi_mode_btn.setText(
            f"● 多选中 ({self._multi_mod_label})" if active else f"多选 ({self._multi_mod_label})"
        )
        _style_multi_toggle_btn(self.multi_mode_btn, self.theme, active, compact=False)
        self.bubble.set_multi_state(visual_active=active, checked=self._sticky_multi_select)

        if active:
            self.footer_hint.setText("● 多选模式已开启：连续点击画面添加 1.1, 1.2, 1.3… · Enter 确认当前组")
            self.footer_hint.setStyleSheet(
                f"color: {self.theme.anchor_line}; font-weight: 600; border: none; background: transparent;"
            )
        else:
            self.footer_hint.setText(self._default_footer_hint())
            self.footer_hint.setStyleSheet(f"color: {self.theme.text_muted}; border: none; background: transparent;")

    def _poll_multi_modifier(self) -> None:
        active = bool(
            self._sticky_multi_select
            or self._tracked_mod_down
            or is_hardware_modifier_held(self._multi_mod_name)
        )
        if active != self._last_multi_active:
            self._update_multi_ui_state()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if not self._mod_poll_timer.isActive():
            self._mod_poll_timer.start()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._mod_poll_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._mod_poll_timer.stop()
        super().closeEvent(event)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        etype = event.type()
        _mod_flag, target_keys = self._target_modifier_spec()

        if etype == QtCore.QEvent.WindowDeactivate:
            if self._tracked_mod_down:
                self._tracked_mod_down = False
                self._update_multi_ui_state()
        elif etype == QtCore.QEvent.KeyPress and isinstance(event, QtGui.QKeyEvent):
            if event.key() in target_keys:
                self._tracked_mod_down = True
                self._update_multi_ui_state()
        elif etype == QtCore.QEvent.KeyRelease and isinstance(event, QtGui.QKeyEvent):
            if event.key() in target_keys:
                self._tracked_mod_down = False
                self._update_multi_ui_state()

        if watched in self._bubble_click_targets and etype in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
            if isinstance(event, QtGui.QMouseEvent) and event.button() == QtCore.Qt.LeftButton:
                if self.is_modifier_key_held(event.modifiers()) and isinstance(watched, QtWidgets.QWidget):
                    canvas_pos = self.canvas.mapFromGlobal(watched.mapToGlobal(event.pos()))
                    self.canvas.handle_canvas_click(canvas_pos, multi_select=True)
                    return True

        if watched is self.header:
            if etype == QtCore.QEvent.MouseButtonPress and isinstance(event, QtGui.QMouseEvent):
                if event.button() == QtCore.Qt.LeftButton and self.header.childAt(event.pos()) in (None, self.brand_lbl, self.meta_lbl):
                    self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
                    return True
            elif etype == QtCore.QEvent.MouseMove and isinstance(event, QtGui.QMouseEvent):
                if self._drag_offset is not None and (event.buttons() & QtCore.Qt.LeftButton):
                    self.move(event.globalPos() - self._drag_offset)
                    return True
            elif etype == QtCore.QEvent.MouseButtonRelease:
                self._drag_offset = None
        return super().eventFilter(watched, event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(self.theme.bg_page))
        painter.setPen(QtGui.QPen(QtGui.QColor(self.theme.border_strong), 1))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.setStyleSheet(build_widget_qss(theme))
        self.header.setStyleSheet(
            f"QFrame {{ background-color: {theme.bg_panel}; border: none; border-bottom: 1px solid {theme.border_hairline}; }}"
        )
        self.footer.setStyleSheet(
            f"QFrame {{ background-color: {theme.bg_panel}; border: none; border-top: 1px solid {theme.border_hairline}; }}"
        )
        self.brand_lbl.setStyleSheet(f"color: {theme.text_primary}; border: none; background: transparent;")
        self.meta_lbl.setStyleSheet(f"color: {theme.text_muted}; border: none; background: transparent;")
        self.toast_lbl.setStyleSheet(f"color: {theme.redline_ins}; border: none; background: transparent;")
        for dot in self._dots:
            dot.setStyleSheet(f"color: {theme.border_strong}; border: none; background: transparent;")

        action_qss = f"""
            QPushButton {{
                border: none; background: transparent; color: {theme.text_secondary};
                font-family: {SERIF_CSS}; font-size: 11.5px; padding: 2px 4px;
            }}
            QPushButton:hover {{ color: {theme.text_primary}; }}
        """
        for btn in (self.undo_btn, self.copy_img_btn, self.copy_md_btn, self.open_web_btn, self.fullscreen_btn):
            btn.setStyleSheet(action_qss)
        self.clear_btn.setStyleSheet(
            f"""
            QPushButton {{
                border: none; background: transparent; color: {theme.text_secondary};
                font-family: {SERIF_CSS}; font-size: 11.5px; padding: 2px 4px;
            }}
            QPushButton:hover {{ color: {theme.redline_del}; }}
            """
        )
        self.export_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {theme.text_primary};
                color: {theme.bg_page};
                border: 1px solid {theme.text_primary};
                border-radius: 0px;
                padding: 3px 10px;
                font-family: {MONO_CSS};
                font-size: 10.5px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {theme.redline_ins};
                border-color: {theme.redline_ins};
                color: #FFFFFF;
            }}
            """
        )
        self.close_btn.setStyleSheet(
            f"""
            QPushButton {{
                border: 1px solid {theme.border_hairline};
                background: transparent;
                color: {theme.text_muted};
                font-family: {MONO_CSS};
                font-size: 12px;
                padding: 0px;
                border-radius: 0px;
            }}
            QPushButton:hover {{ border-color: {theme.border_strong}; color: {theme.text_primary}; }}
            """
        )
        self.bubble.apply_theme(theme)
        self.canvas.set_theme(theme)
        self._update_multi_ui_state()

    def _cropped_raw_qimage(self) -> QtGui.QImage:
        return self._cropped_pixmap.toImage()

    def build_som_image(self) -> QtGui.QImage:
        return render_som_image(
            self._cropped_raw_qimage(),
            self.session,
            theme=self.theme,
            highlight_active=False,
            pin_scale=self._pin_scale,
            pin_theme=self._pin_theme,
        )

    def _position_bubble(self, focus_editor: bool = False) -> None:
        active = self.session.active_thread
        if active is None or not active.pins:
            self.bubble.hide()
            return

        self.bubble.set_thread(active)
        bounds = self.rect().adjusted(8, self.HEADER_H + 6, -8, -(self.FOOTER_H + 6))
        if bounds.height() < self.bubble.height() + 4:
            bounds = self.rect().adjusted(6, 6, -6, -6)

        pts = [self.canvas.mapToParent(self.canvas.crop_to_canvas(p.x, p.y).toPoint()) for p in active.pins]
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        mid_x = (min_x + max_x) // 2
        mid_y = (min_y + max_y) // 2

        pin_avoid = QtCore.QRect(min_x - 20, min_y - 30, (max_x - min_x) + 74, (max_y - min_y) + 52)
        bw, bh = self.bubble.width(), self.bubble.height()

        if len(active.pins) > 1 and (max_x - min_x) > 100:
            candidates = [
                QtCore.QPoint(mid_x - bw // 2, max_y + 42),
                QtCore.QPoint(mid_x - bw // 2, min_y - bh - 38),
                QtCore.QPoint(max_x + 38, mid_y - bh // 2),
                QtCore.QPoint(min_x - bw - 30, mid_y - bh // 2),
            ]
        else:
            candidates = [
                QtCore.QPoint(max_x + 36, mid_y - 20),
                QtCore.QPoint(min_x - bw - 30, mid_y - 20),
                QtCore.QPoint(mid_x - bw // 2, max_y + 40),
                QtCore.QPoint(mid_x - bw // 2, min_y - bh - 38),
            ]

        img_rect = self.canvas.image_rect()
        if img_rect.height() < self.canvas.height() - 120:
            img_top = self.canvas.y() + img_rect.top()
            img_bottom = self.canvas.y() + img_rect.bottom()
            clamped_x = max(bounds.left(), min(mid_x - bw // 2, bounds.right() - bw))
            candidates = [
                QtCore.QPoint(clamped_x, img_bottom + 5),
                QtCore.QPoint(clamped_x, img_top - bh - 5),
                *candidates,
            ]

        chosen = None
        for pt in candidates:
            cand_rect = QtCore.QRect(pt, QtCore.QSize(bw, bh))
            if bounds.contains(cand_rect) and not cand_rect.intersects(pin_avoid):
                chosen = pt
                break
        if chosen is None:
            for pt in candidates:
                cand_rect = QtCore.QRect(pt, QtCore.QSize(bw, bh))
                if bounds.contains(cand_rect):
                    chosen = pt
                    break
        if chosen is None:
            cx = max(bounds.left(), min(candidates[0].x(), bounds.right() - bw))
            cy = max(bounds.top(), min(candidates[0].y(), bounds.bottom() - bh))
            chosen = QtCore.QPoint(cx, cy)

        self.bubble.move(chosen)
        self.bubble.show()
        self.bubble.raise_()
        if focus_editor:
            QtCore.QTimer.singleShot(0, lambda: self.bubble.editor.setFocus(QtCore.Qt.OtherFocusReason))

    def sync_ui(self, focus_editor: bool = False) -> None:
        self.meta_lbl.setText(f"{self.session.crop_width}×{self.session.crop_height} · {len(self.session.threads)} 处标注")
        self._position_bubble(focus_editor=focus_editor)
        self.canvas.update()

    def _on_canvas_pin_clicked(self, crop_x: float, crop_y: float, alt_held: bool) -> None:
        self.session.add_click(crop_x, crop_y, alt_held=alt_held)
        self.sync_ui(focus_editor=True)

    def _on_existing_pin_clicked(self, thread_id: str) -> None:
        self.session.reopen_thread(thread_id)
        self.sync_ui(focus_editor=True)

    def _on_comment_edited(self, thread_id: str, comment: str) -> None:
        self.session.set_comment(thread_id, comment)

    def _on_delete_thread(self, thread_id: str) -> None:
        self.session.delete_thread(thread_id)
        self.sync_ui(focus_editor=False)

    def _on_pin_moved(self, thread_id: str) -> None:
        if self.session.active_thread_id == thread_id:
            self._position_bubble(focus_editor=False)

    def _on_delete_pin(self, pin_id: str) -> None:
        if self.session.delete_pin(pin_id):
            self.sync_ui(focus_editor=False)
            self._show_toast("已删除该标点")

    def commit_active_thread(self) -> None:
        self._sticky_multi_select = False
        self.session.commit_active_thread()
        self._update_multi_ui_state()
        self.sync_ui(focus_editor=False)
        self.setFocus()

    def dismiss_active_bubble(self) -> None:
        self._sticky_multi_select = False
        self.session.hide_active_bubble()
        self._update_multi_ui_state()
        self.sync_ui(focus_editor=False)
        self.setFocus()

    def undo_last_pin(self) -> None:
        if self.session.undo_last_pin():
            self.sync_ui(focus_editor=(self.session.active_thread is not None))

    def clear_all(self) -> None:
        self._sticky_multi_select = False
        self.session.clear()
        self._update_multi_ui_state()
        self.sync_ui(focus_editor=False)

    def _show_toast(self, message: str, duration_ms: int = 2400) -> None:
        self.toast_lbl.setText(message)
        QtCore.QTimer.singleShot(duration_ms, lambda: self.toast_lbl.setText("") if self.isVisible() else None)

    def _export(self, mode: str, close_after: bool) -> ExportResult | None:
        raw = self._cropped_raw_qimage()
        if raw.isNull():
            return None
        som = self.build_som_image()
        md = self.session.format_vlm_prompt()
        result = export_session(
            som_image=som,
            raw_image=raw,
            prompt_markdown=md,
            mode=mode,
            state_dir=self.state_dir,
            copy_clipboard=self.copy_clipboard,
            keep_history=self._keep_history_count,
        )
        if close_after:
            self.close()
        else:
            if mode in {"prompt", "markdown"}:
                self._show_toast("已复制指令")
            elif mode == "image":
                self._show_toast("已复制图片")
            else:
                self._show_toast(f"已保存 {result.latest_png.name}")
        return result

    def toggle_fullscreen(self) -> None:
        if not self._is_fullscreen:
            self._normal_pos = self.pos()
            screen = QtWidgets.QApplication.screenAt(self.geometry().center()) or QtWidgets.QApplication.primaryScreen()
            geo = screen.geometry() if screen is not None else QtCore.QRect(0, 0, 1440, 900)
            stage_w = max(self.MIN_WIN_W, geo.width() - 2)
            stage_h = max(240, geo.height() - self.HEADER_H - self.FOOTER_H - 2)
            avail_w = max(1, stage_w - 2 * self.CANVAS_PAD)
            avail_h = max(1, stage_h - 2 * self.CANVAS_PAD)
            ratio = min(avail_w / float(self.session.crop_width), avail_h / float(self.session.crop_height))
            new_disp_w = max(1, round(self.session.crop_width * ratio))
            new_disp_h = max(1, round(self.session.crop_height * ratio))

            self.canvas.set_display_size(QtCore.QSize(new_disp_w, new_disp_h), QtCore.QSize(stage_w, stage_h))
            self.setFixedSize(geo.size())
            self.move(geo.topLeft())
            self._is_fullscreen = True
            self.fullscreen_btn.setText("还原")
        else:
            self.canvas.set_display_size(self._normal_disp_size, self._normal_canvas_size)
            self.setFixedSize(self._normal_win_size)
            if self._normal_pos is not None:
                self.move(self._normal_pos)
            self._is_fullscreen = False
            self.fullscreen_btn.setText("全屏")

        if self.session.active_thread is not None:
            self._position_bubble(focus_editor=False)
        self.raise_()

    def open_in_browser(self) -> Path | None:
        raw = self._cropped_raw_qimage()
        if raw.isNull():
            return None
        som = self.build_som_image()
        md = self.session.format_vlm_prompt()
        threads_data = [
            {
                "id": t.id,
                "number": t.number,
                "comment": t.comment,
                "pins": [{"id": p.id, "x": p.x, "y": p.y} for p in t.pins],
            }
            for t in self.session.threads
        ]
        html_path = export_html_preview(
            som_image=som,
            raw_image=raw,
            prompt_markdown=md,
            threads_data=threads_data,
            theme_name=self.theme.name,
            pin_theme=self._pin_theme,
            pin_scale=self._pin_scale,
            show_leader_lines=self._show_leader_lines,
            state_dir=self.state_dir,
            keep_history=self._keep_history_count,
            open_browser=True,
        )
        if self._is_fullscreen:
            self.toggle_fullscreen()
        self._show_toast("已在网页打开")
        return html_path

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()

        if key == QtCore.Qt.Key_F11:
            self.toggle_fullscreen()
            event.accept()
            return
        if (mods & QtCore.Qt.ControlModifier) and key == QtCore.Qt.Key_O:
            self.open_in_browser()
            event.accept()
            return
        if key == QtCore.Qt.Key_Escape:
            if self.session.active_thread is not None:
                self.dismiss_active_bubble()
            elif self._is_fullscreen:
                self.toggle_fullscreen()
            else:
                self.close()
            event.accept()
            return
        if (mods & QtCore.Qt.ControlModifier) and key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self._export(self._export_mode, close_after=True)
            event.accept()
            return
        if (mods & QtCore.Qt.ControlModifier) and key == QtCore.Qt.Key_Z:
            self.undo_last_pin()
            event.accept()
            return
        super().keyPressEvent(event)


# Clean alias for external callers
PinShotOverlay = PinShotPopup


class ScreenCropSelector(QtWidgets.QWidget):
    """Phase 1 fullscreen region selector that extracts a cropped QPixmap and launches PinShotPopup."""

    regionSelected = QtCore.pyqtSignal(QtGui.QPixmap)

    def __init__(self, backdrop: QtGui.QPixmap, theme_name: str | None = None, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._backdrop = backdrop
        self._dpr = max(1.0, float(backdrop.devicePixelRatio()))
        effective_theme = resolve_theme_name(load_config()["theme"] if theme_name is None else theme_name)
        self.theme = get_theme(effective_theme)
        logical_size = QtCore.QSize(round(backdrop.width() / self._dpr), round(backdrop.height() / self._dpr))
        self.setFixedSize(logical_size)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        self._dragging = False
        self._drag_start = QtCore.QPoint()
        self.crop_rect = QtCore.QRect()
        self._hover_pos = QtCore.QPoint(-1, -1)

    @classmethod
    def capture_desktop(cls, theme_name: str | None = None) -> "ScreenCropSelector":
        app = QtWidgets.QApplication.instance()
        screens = app.screens() if app else []
        primary = app.primaryScreen() if app else None
        if not screens:
            raise RuntimeError("No display screen found by Qt.")

        virtual_rect = QtCore.QRect()
        for scr in screens:
            virtual_rect = virtual_rect.united(scr.geometry())

        max_dpr = max((scr.devicePixelRatio() for scr in screens), default=1.0)
        canvas = QtGui.QPixmap(round(virtual_rect.width() * max_dpr), round(virtual_rect.height() * max_dpr))
        canvas.setDevicePixelRatio(max_dpr)
        canvas.fill(QtGui.QColor("#121211"))

        painter = QtGui.QPainter(canvas)
        for scr in screens:
            shot = scr.grabWindow(0)
            target = scr.geometry().translated(-virtual_rect.topLeft())
            painter.drawPixmap(target, shot)
        painter.end()

        if canvas.isNull() and primary is not None:
            canvas = primary.grabWindow(0)

        selector = cls(backdrop=canvas, theme_name=theme_name)
        selector.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        selector.setGeometry(virtual_rect)
        return selector

    def _emit_crop(self, rect: QtCore.QRect) -> None:
        scaled = QtCore.QRect(
            round(rect.x() * self._dpr),
            round(rect.y() * self._dpr),
            round(rect.width() * self._dpr),
            round(rect.height() * self._dpr),
        ).intersected(self._backdrop.rect())
        cropped = self._backdrop.copy(scaled)
        self.regionSelected.emit(cropped)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.RightButton:
            self.close()
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self._drag_start = event.pos()
            self.crop_rect = QtCore.QRect(event.pos(), event.pos())
            self.update()
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self._hover_pos = event.pos()
        if self._dragging:
            self.crop_rect = QtCore.QRect(self._drag_start, event.pos()).normalized().intersected(self.rect())
        self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging and event.button() == QtCore.Qt.LeftButton:
            self._dragging = False
            rect = QtCore.QRect(self._drag_start, event.pos()).normalized().intersected(self.rect())
            if rect.width() >= 24 and rect.height() >= 24:
                self._emit_crop(rect)
            else:
                self.crop_rect = QtCore.QRect()
                self.update()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()
            event.accept()
            return
        if event.key() == QtCore.Qt.Key_F:
            app = QtWidgets.QApplication.instance()
            target_rect = self.rect()
            if app and self._hover_pos.x() >= 0:
                gpos = self.mapToGlobal(self._hover_pos)
                for scr in app.screens():
                    if scr.geometry().contains(gpos):
                        target_rect = QtCore.QRect(self.mapFromGlobal(scr.geometry().topLeft()), scr.geometry().size()).intersected(self.rect())
                        break
            self._emit_crop(target_rect.adjusted(24, 24, -24, -24))
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.drawPixmap(self.rect(), self._backdrop)
        scrim = QtGui.QColor(*self.theme.scrim_rgba)

        if self.crop_rect.isNull():
            painter.fillRect(self.rect(), scrim)
            if self._hover_pos.x() >= 0:
                painter.setPen(QtGui.QPen(QtGui.QColor(self.theme.anchor_line), 1, QtCore.Qt.DotLine))
                painter.drawLine(0, self._hover_pos.y(), self.width(), self._hover_pos.y())
                painter.drawLine(self._hover_pos.x(), 0, self._hover_pos.x(), self.height())
            painter.end()
            return

        region = QtGui.QRegion(self.rect()) - QtGui.QRegion(self.crop_rect)
        painter.setClipRegion(region)
        painter.fillRect(self.rect(), scrim)
        painter.setClipping(False)

        painter.setPen(QtGui.QPen(QtGui.QColor(self.theme.anchor_line), 1.5))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(self.crop_rect.adjusted(-1, -1, 0, 0))

        painter.setPen(QtGui.QPen(QtGui.QColor(self.theme.anchor_line), 1))
        painter.setBrush(QtGui.QColor(self.theme.bg_panel))
        for pt in (
            self.crop_rect.topLeft(),
            self.crop_rect.topRight(),
            self.crop_rect.bottomLeft(),
            self.crop_rect.bottomRight(),
        ):
            painter.drawRect(QtCore.QRect(pt.x() - 3, pt.y() - 3, 6, 6))

        dim_text = f"{self.crop_rect.width()} × {self.crop_rect.height()}"
        font = mono_font(8.8, QtGui.QFont.Bold)
        painter.setFont(font)
        fm = QtGui.QFontMetrics(font)
        tw = fm.horizontalAdvance(dim_text) + 14
        th = 22
        by = min(self.height() - th - 4, self.crop_rect.bottom() + 6)
        badge = QtCore.QRect(max(4, self.crop_rect.right() - tw), by, tw, th)
        painter.fillRect(badge, QtGui.QColor(self.theme.pin_fill))
        painter.setPen(QtGui.QColor(self.theme.pin_text))
        painter.drawText(badge, QtCore.Qt.AlignCenter, dim_text)
        painter.end()


def build_synthetic_desktop_pixmap(width: int = 1280, height: int = 800) -> QtGui.QPixmap:
    """Create a realistic synthetic workspace desktop backdrop for offscreen visual QA demos."""
    pix = QtGui.QPixmap(width, height)
    pix.fill(QtGui.QColor("#EDEBE4"))
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)

    p.fillRect(0, 0, width, 28, QtGui.QColor("#191817"))
    p.setPen(QtGui.QColor("#ECEAE4"))
    p.setFont(mono_font(8.5))
    p.drawText(16, 19, "Ubuntu · DX Workspace  |  Chrome · Target App Preview")

    app_rect = QtCore.QRect(160, 100, 900, 560)
    p.fillRect(app_rect, QtGui.QColor("#FFFFFF"))
    p.setPen(QtGui.QPen(QtGui.QColor("#D8D4CA"), 1))
    p.drawRect(app_rect)

    p.fillRect(160, 100, 900, 36, QtGui.QColor("#F5F4F0"))
    p.drawLine(160, 136, 1060, 136)
    p.setPen(QtGui.QColor("#78746C"))
    p.setFont(mono_font(8.8))
    p.drawText(180, 123, "https://app.dx-workspace.local/dashboard/synthesis")

    p.fillRect(161, 137, 190, 522, QtGui.QColor("#FAF9F6"))
    p.setPen(QtGui.QColor("#E6E2D8"))
    p.drawLine(351, 137, 351, 659)
    p.setPen(QtGui.QColor("#191817"))
    p.setFont(serif_font(11.5, QtGui.QFont.Bold))
    p.drawText(180, 174, "Proxy2Policy")
    p.setFont(serif_font(9.6))
    for i, nav in enumerate(["01 · Pipeline", "02 · Rollouts", "03 · Rewards", "04 · Traces"]):
        y = 212 + i * 30
        if i == 1:
            p.fillRect(172, y - 15, 162, 23, QtGui.QColor("#EAE6DC"))
            p.setPen(QtGui.QColor("#191817"))
        else:
            p.setPen(QtGui.QColor("#575551"))
        p.drawText(182, y, nav)

    p.setPen(QtGui.QColor("#191817"))
    p.setFont(serif_font(15.0, QtGui.QFont.Bold))
    p.drawText(380, 182, "Policy Distillation · Batch #482")
    p.setPen(QtGui.QColor("#6E6B64"))
    p.setFont(mono_font(8.5))
    p.drawText(380, 204, "DATASET: synthetic-v4  |  STATUS: converged")

    cta = QtCore.QRect(906, 164, 128, 32)
    p.fillRect(cta, QtGui.QColor("#191817"))
    p.setPen(QtGui.QColor("#FAF9F5"))
    p.setFont(mono_font(8.5, QtGui.QFont.Bold))
    p.drawText(cta, QtCore.Qt.AlignCenter, "Deploy Model")

    metrics = [
        ("Win Rate", "84.2%", "+6.1%"),
        ("KL Drift", "0.019", "ok"),
        ("Mean Tokens", "1,420", "-18%"),
        ("VLM Score", "9.14", "verified"),
    ]
    for idx, (title, val, sub) in enumerate(metrics):
        cx = 380 + idx * 165
        card = QtCore.QRect(cx, 236, 152, 96)
        p.fillRect(card, QtGui.QColor("#FAF9F5"))
        p.setPen(QtGui.QPen(QtGui.QColor("#DCD8CE"), 1))
        p.drawRect(card)
        p.setPen(QtGui.QColor("#6E6B64"))
        p.setFont(mono_font(8.0))
        p.drawText(cx + 12, 256, title.upper())
        p.setPen(QtGui.QColor("#191817"))
        p.setFont(serif_font(16.0, QtGui.QFont.Bold))
        p.drawText(cx + 12, 290, val)
        p.setPen(QtGui.QColor("#2D6A4F"))
        p.setFont(mono_font(8.0))
        p.drawText(cx + 12, 318, sub)

    chart = QtCore.QRect(380, 356, 654, 276)
    p.fillRect(chart, QtGui.QColor("#FAF9F5"))
    p.setPen(QtGui.QPen(QtGui.QColor("#E6E2D8"), 1))
    p.drawRect(chart)
    p.setPen(QtGui.QColor("#191817"))
    p.setFont(mono_font(8.5, QtGui.QFont.Bold))
    p.drawText(396, 380, "VALIDATION LOSS TRAJECTORY (0 -> 12K)")

    pts = [
        QtCore.QPointF(406, 594),
        QtCore.QPointF(500, 535),
        QtCore.QPointF(605, 495),
        QtCore.QPointF(720, 452),
        QtCore.QPointF(850, 424),
        QtCore.QPointF(1005, 408),
    ]
    p.setPen(QtGui.QPen(QtGui.QColor("#B89B67"), 2.0))
    for a, b in zip(pts, pts[1:]):
        p.drawLine(a, b)

    p.end()
    return pix


def _compose_popup_on_desktop(desktop: QtGui.QPixmap, popup: PinShotPopup) -> QtGui.QPixmap:
    """Composite `popup.grab()` centered over a subtly dimmed desktop frame for realistic visual QA."""
    frame = QtGui.QPixmap(desktop.size())
    painter = QtGui.QPainter(frame)
    painter.drawPixmap(0, 0, desktop)
    painter.fillRect(frame.rect(), QtGui.QColor(25, 24, 23, 88))

    pop_pix = popup.grab()
    px = max(16, (frame.width() - pop_pix.width()) // 2)
    py = max(16, (frame.height() - pop_pix.height()) // 2)

    # Subtle ambient elevation shadow under the floating popup
    painter.fillRect(QtCore.QRect(px + 6, py + 10, pop_pix.width(), pop_pix.height()), QtGui.QColor(25, 24, 23, 52))
    painter.fillRect(QtCore.QRect(px + 2, py + 4, pop_pix.width(), pop_pix.height()), QtGui.QColor(25, 24, 23, 36))
    painter.drawPixmap(px, py, pop_pix)
    painter.end()
    return frame


def render_demo_screens(output_dir: Path, theme_name: str = "paper") -> list[Path]:
    """Render offscreen demo PNGs showcasing the floating popup UX and exported Set-of-Mark image."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["dx-pinshot-demo"])
    _ = app

    desktop = build_synthetic_desktop_pixmap(1280, 800)
    cropped = desktop.copy(QtCore.QRect(160, 100, 900, 560))
    saved: list[Path] = []

    # Screen 1: Single-pin commenting inside floating PinShotPopup
    pop1 = PinShotPopup(cropped_pixmap=cropped, theme_name=theme_name, copy_clipboard=False)
    t1, _ = pop1.session.add_click(810, 80, alt_held=False)
    pop1.session.set_comment(t1.id, "Deploy 按钮与左侧标题基线差 4px。")
    pop1.commit_active_thread()
    t2, _ = pop1.session.add_click(110, 114, alt_held=False)
    pop1.session.set_comment(t2.id, "侧栏高亮改为 #F3F1EA 并加 2px #B89B67 指示线。")
    pop1.sync_ui(focus_editor=False)
    app.processEvents()
    p1 = out / "screen1_single.png"
    _compose_popup_on_desktop(desktop, pop1).save(str(p1), "PNG")
    saved.append(p1)
    pop1.deleteLater()

    # Screen 2: Alt+Click multi-select grouped commenting inside floating PinShotPopup
    pop2 = PinShotPopup(cropped_pixmap=cropped, theme_name=theme_name, copy_clipboard=False)
    tg1, _ = pop2.session.add_click(810, 80, alt_held=False)
    pop2.session.set_comment(tg1.id, "Deploy 按钮与主标题基线对齐。")
    pop2.commit_active_thread()
    tg2, _ = pop2.session.add_click(305, 184, alt_held=True)
    pop2.session.add_click(470, 184, alt_held=True)
    pop2.session.add_click(635, 184, alt_held=True)
    pop2.session.add_click(800, 184, alt_held=True)
    pop2.session.set_comment(tg2.id, "这组 4 个指标卡片统一强化边框为 1px #9E988E。")
    pop2.sync_ui(focus_editor=False)
    app.processEvents()
    p2 = out / "screen2_alt_group.png"
    _compose_popup_on_desktop(desktop, pop2).save(str(p2), "PNG")
    saved.append(p2)

    # Screen 3: Exported clean Set-of-Mark image (zero obscuring comment boxes)
    som_img = pop2.build_som_image()
    p3 = out / "screen3_clean_som.png"
    som_img.save(str(p3), "PNG")
    saved.append(p3)
    pop2.deleteLater()

    return saved
