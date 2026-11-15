"""Tests for DX PinShot core annotation model, SoM image rendering, Markdown export, GUI overlay, and hotkey logic."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtGui, QtTest, QtWidgets

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dx_pinshot.clipboard import export_session
from dx_pinshot.hotkey import get_hotkey_status, install_hotkey, uninstall_hotkey
from dx_pinshot.model import AnnotationSession, render_som_image
from dx_pinshot.overlay import PinShotPopup, ScreenCropSelector, build_synthetic_desktop_pixmap
from dx_pinshot.tokens import PAPER_THEME


_QAPP: QtWidgets.QApplication | None = None


def _ensure_qapp() -> QtWidgets.QApplication:
    global _QAPP
    inst = QtWidgets.QApplication.instance()
    if inst is not None:
        _QAPP = inst
        return inst
    _QAPP = QtWidgets.QApplication(["pytest-dx-pinshot"])
    return _QAPP


def test_single_pin_numbering_1_to_5() -> None:
    _ensure_qapp()
    session = AnnotationSession(1200, 800)

    for idx, (x, y) in enumerate([(100, 100), (220, 140), (350, 210), (480, 300), (610, 420)], start=1):
        thread, pin = session.add_click(x, y, alt_held=False)
        session.set_comment(thread.id, f"Issue {idx}")
        assert thread.number == idx
        assert pin.label == str(idx)
        assert not thread.is_multi_pin

    assert [t.labels for t in session.threads] == [["1"], ["2"], ["3"], ["4"], ["5"]]
    prompt = session.format_vlm_prompt()
    for idx in range(1, 6):
        assert f"**[{idx}]**" in prompt
        assert f"Issue {idx}" in prompt


def test_alt_click_grouping_without_open_slip() -> None:
    _ensure_qapp()
    session = AnnotationSession(1200, 800)

    # First group directly via Alt+Click -> 1.1, 1.2, 1.3, 1.4
    t1, p1 = session.add_click(100, 200, alt_held=True)
    _, p2 = session.add_click(260, 200, alt_held=True)
    _, p3 = session.add_click(420, 200, alt_held=True)
    _, p4 = session.add_click(580, 200, alt_held=True)
    session.set_comment(t1.id, "Align all four cards")
    session.commit_active_thread()

    assert [p.label for p in t1.pins] == ["1.1", "1.2", "1.3", "1.4"]
    assert p1.label == "1.1" and p4.label == "1.4"

    # Next Alt+Click after slip closed starts a brand new group 2.1, 2.2, 2.3
    t2, _ = session.add_click(120, 450, alt_held=True)
    session.add_click(280, 450, alt_held=True)
    session.add_click(440, 450, alt_held=True)
    session.set_comment(t2.id, "Fix footer spacing")

    assert t1.id != t2.id
    assert t2.labels == ["2.1", "2.2", "2.3"]

    prompt = session.format_vlm_prompt()
    assert "**[1.1, 1.2, 1.3, 1.4]** ((100,200), (260,200), (420,200), (580,200)): Align all four cards" in prompt
    assert "**[2.1, 2.2, 2.3]** ((120,450), (280,450), (440,450)): Fix footer spacing" in prompt


def test_active_single_pin_promotion_to_grouped() -> None:
    _ensure_qapp()
    session = AnnotationSession(1000, 700)

    t1, _ = session.add_click(80, 90, alt_held=False)
    session.set_comment(t1.id, "Header padding")
    session.commit_active_thread()

    # User clicks pin 2 normally, then realizes they want Alt+Click multi-select on the active slip
    t2, p2a = session.add_click(300, 300, alt_held=False)
    assert p2a.label == "2"

    t2_again, p2b = session.add_click(500, 300, alt_held=True)
    assert t2_again.id == t2.id
    assert t2.labels == ["2.1", "2.2"]
    assert session.threads[0].labels == ["1"]


def test_undo_and_delete_renumbering() -> None:
    _ensure_qapp()
    session = AnnotationSession(1000, 700)

    t1, _ = session.add_click(50, 50, alt_held=False)
    session.commit_active_thread()
    t2, _ = session.add_click(150, 50, alt_held=False)
    session.add_click(250, 50, alt_held=True)
    session.commit_active_thread()
    t3, _ = session.add_click(350, 50, alt_held=False)

    assert session.undo_last_pin() is True
    assert len(session.threads) == 2

    # Undo 2.2 -> leaves single pin in t2 (still grouped because created as group)
    assert session.undo_last_pin() is True
    assert session.threads[1].labels == ["2.1"]

    # Delete first thread -> second thread promotes from 2.x to 1.x cleanly
    assert session.delete_thread(t1.id) is True
    assert len(session.threads) == 1
    assert session.threads[0].labels == ["1.1"]


def test_render_som_image_bakes_clean_markers() -> None:
    _ensure_qapp()
    base = QtGui.QImage(640, 360, QtGui.QImage.Format_ARGB32)
    base.fill(QtGui.QColor("#FFFFFF"))

    session = AnnotationSession(640, 360)
    t1, _ = session.add_click(120, 120, alt_held=False)
    session.set_comment(t1.id, "Should NOT appear as text bubble inside SoM image")
    session.commit_active_thread()
    t2, _ = session.add_click(320, 180, alt_held=True)
    session.add_click(420, 180, alt_held=True)

    som = render_som_image(base, session, theme=PAPER_THEME)
    assert som.size() == base.size()

    # Pixels near pin (120, 120) and pin (420, 180) must differ from pure white
    def _has_ink_around(cx: int, cy: int) -> bool:
        for dx in range(-30, 40):
            for dy in range(-30, 30):
                px = max(0, min(639, cx + dx))
                py = max(0, min(359, cy + dy))
                if QtGui.QColor(som.pixel(px, py)) != QtGui.QColor("#FFFFFF"):
                    return True
        return False

    assert _has_ink_around(120, 120)
    assert _has_ink_around(420, 180)

    # Far bottom-left blank corner must remain completely untouched white (no huge comment box!)
    assert QtGui.QColor(som.pixel(20, 330)) == QtGui.QColor("#FFFFFF")


def test_overlay_interactions_and_export(tmp_path: Path) -> None:
    app = _ensure_qapp()
    backdrop = build_synthetic_desktop_pixmap(1280, 720)
    crop = QtCore.QRect(100, 80, 800, 500)

    # Verify Phase 1 ScreenCropSelector emits cropped pixmap matching selected region
    selector = ScreenCropSelector(backdrop=backdrop, theme_name="paper")
    emitted: list[QtGui.QPixmap] = []
    selector.regionSelected.connect(emitted.append)
    selector.show()
    app.processEvents()
    QtTest.QTest.mousePress(selector, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, crop.topLeft())
    QtTest.QTest.mouseMove(selector, crop.bottomRight())
    QtTest.QTest.mouseRelease(selector, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, crop.bottomRight())
    app.processEvents()
    selector.close()
    assert len(emitted) == 1
    assert emitted[0].width() >= 799 and emitted[0].height() >= 499

    # Verify Phase 2 independent floating PinShotPopup interactions
    popup = PinShotPopup(
        cropped_pixmap=emitted[0],
        theme_name="paper",
        state_dir=tmp_path,
        copy_clipboard=False,
    )
    popup.show()
    app.processEvents()

    img_origin = popup.canvas.image_rect().topLeft()
    p1_click = img_origin + QtCore.QPoint(120, 100)
    p2_click = img_origin + QtCore.QPoint(280, 100)

    # Click inside image stage -> places [1] and opens compact PinCommentBubble
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, p1_click)
    app.processEvents()
    assert len(popup.session.threads) == 1
    assert popup.session.threads[0].labels == ["1"]
    assert popup.bubble.isVisible()
    assert popup.rect().contains(popup.bubble.geometry())
    assert abs(popup.session.threads[0].pins[0].x - 120) <= 1.0
    assert abs(popup.session.threads[0].pins[0].y - 100) <= 1.0

    # Alt+Click -> promotes [1] to [1.1, 1.2] inside the same bubble
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, p2_click)
    app.processEvents()
    assert len(popup.session.threads) == 1
    assert popup.session.threads[0].labels == ["1.1", "1.2"]
    assert "[1.1, 1.2]" in popup.bubble.badge_label.text()
    assert popup.rect().contains(popup.bubble.geometry())

    popup.bubble.editor.setPlainText("Group alignment fix")
    QtTest.QTest.keyClick(popup.bubble.editor, QtCore.Qt.Key_Return)
    app.processEvents()
    assert popup.session.active_thread is None
    assert not popup.bubble.isVisible()

    # Large screenshot scaling keeps coordinate mapping exact and keeps bubble clamped inside popup
    large_shot = QtGui.QPixmap(1800, 1200)
    large_shot.fill(QtGui.QColor("#FFFFFF"))
    large_popup = PinShotPopup(cropped_pixmap=large_shot, state_dir=tmp_path, copy_clipboard=False)
    large_popup.show()
    app.processEvents()
    edge_pt = large_popup.canvas.image_rect().bottomRight() - QtCore.QPoint(12, 12)
    QtTest.QTest.mouseClick(large_popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, edge_pt)
    app.processEvents()
    assert large_popup.bubble.isVisible()
    assert large_popup.rect().contains(large_popup.bubble.geometry())
    assert large_popup.session.threads[0].pins[0].x > 1700
    large_popup.close()

    # Export artifacts cleanly into tmp_path
    res = popup._export("both", close_after=False)
    assert res is not None
    assert res.latest_png.exists()
    assert res.latest_raw_png.exists()
    assert res.latest_md.exists()
    md_text = res.latest_md.read_text(encoding="utf-8")
    assert "**[1.1, 1.2]**" in md_text
    assert "Group alignment fix" in md_text
    popup.close()


def test_hotkey_backup_install_uninstall(tmp_path: Path) -> None:
    store = {
        ("org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings"):
            "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom2/']",
        ("org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom2/", "name"):
            "'Flameshot 区域截图'",
        ("org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom2/", "command"):
            "'flameshot gui'",
        ("org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom2/", "binding"):
            "'<Primary><Shift>a'",
    }

    def fake_runner(*args: str) -> str:
        op, schema, key = args[0], args[1], args[2]
        if op == "get":
            return store[(schema, key)]
        if op == "set":
            val = args[3]
            store[(schema, key)] = val if val.startswith("[") else repr(val)
            return ""
        raise AssertionError(op)

    backup_file = tmp_path / "hotkey_backup.json"
    target_cmd = "/opt/dx-pinshot/bin/dx-pinshot gui"

    installed = install_hotkey(target_command=target_cmd, binding="<Primary><Shift>a", backup_path=backup_file, runner=fake_runner)
    assert installed["installed"] is True
    assert backup_file.exists()
    saved_backup = json.loads(backup_file.read_text(encoding="utf-8"))
    assert saved_backup["previous"]["command"] == "flameshot gui"

    status = get_hotkey_status(target_command=target_cmd, binding="<Primary><Shift>a", backup_path=backup_file, runner=fake_runner)
    assert status["active_is_dx_pinshot"] is True
    assert status["backup_previous_command"] == "flameshot gui"

    restored = uninstall_hotkey(backup_path=backup_file, runner=fake_runner)
    assert restored["restored_command"] == "flameshot gui"
    assert restored["restored_name"] == "Flameshot 区域截图"
    status_after = get_hotkey_status(target_command=target_cmd, binding="<Primary><Shift>a", backup_path=backup_file, runner=fake_runner)
    assert status_after["active_is_dx_pinshot"] is False


def test_alt_click_through_open_bubble_and_rapid_dblclick(tmp_path: Path) -> None:
    app = _ensure_qapp()
    shot = QtGui.QPixmap(760, 480)
    shot.fill(QtGui.QColor("#FAF9F5"))

    popup = PinShotPopup(cropped_pixmap=shot, state_dir=tmp_path, copy_clipboard=False)
    popup.show()
    app.processEvents()

    img_rect = popup.canvas.image_rect()
    first_pt = QtCore.QPoint(img_rect.left() + 180, img_rect.top() + 180)
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, first_pt)
    app.processEvents()

    assert popup.bubble.isVisible()
    assert len(popup.session.threads) == 1

    # Click directly on the open bubble's editor viewport while holding Alt -> clicks through to canvas!
    vp = popup.bubble.editor.viewport()
    vp_center = vp.rect().center()
    QtTest.QTest.mouseClick(vp, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, vp_center)
    app.processEvents()

    assert len(popup.session.threads) == 1
    assert popup.session.threads[0].labels == ["1.1", "1.2"]

    # Rapid second Alt+Click delivered as MouseButtonDblClick should also append 1.3
    dbl_pt = QtCore.QPoint(img_rect.left() + 260, img_rect.top() + 220)
    QtTest.QTest.mouseDClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, dbl_pt)
    app.processEvents()

    assert len(popup.session.threads) == 1
    assert popup.session.threads[0].labels == ["1.1", "1.2", "1.3"]
    popup.close()


def test_alt_click_promotes_uncommitted_empty_single_pin(tmp_path: Path) -> None:
    app = _ensure_qapp()
    shot = QtGui.QPixmap(760, 480)
    shot.fill(QtGui.QColor("#FAF9F5"))

    popup = PinShotPopup(cropped_pixmap=shot, state_dir=tmp_path, copy_clipboard=False)
    popup.show()
    app.processEvents()

    img_rect = popup.canvas.image_rect()
    p1 = QtCore.QPoint(img_rect.left() + 120, img_rect.top() + 120)
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, p1)
    app.processEvents()
    assert popup.session.threads[0].labels == ["1"]

    # User dismisses empty bubble with Esc/right-click, then realizes they want Alt+Click multi-select
    QtTest.QTest.keyClick(popup.bubble.editor, QtCore.Qt.Key_Escape)
    app.processEvents()
    assert popup.session.active_thread is None
    assert not popup.bubble.isVisible()

    p2 = QtCore.QPoint(img_rect.left() + 220, img_rect.top() + 140)
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, p2)
    app.processEvents()

    assert len(popup.session.threads) == 1
    assert popup.session.threads[0].labels == ["1.1", "1.2"]
    assert popup.bubble.isVisible()

    # Once confirmed via Enter/commit, a subsequent Alt+Click starts a fresh group [2.1]
    popup.session.set_comment(popup.session.threads[0].id, "Keep both aligned")
    popup.commit_active_thread()
    p3 = QtCore.QPoint(img_rect.left() + 360, img_rect.top() + 200)
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, p3)
    app.processEvents()

    assert len(popup.session.threads) == 2
    assert popup.session.threads[1].labels == ["2.1"]
    popup.close()


def test_configurable_multi_select_modifier_shift_and_ctrl(tmp_path: Path) -> None:
    app = _ensure_qapp()
    shot = QtGui.QPixmap(760, 480)
    shot.fill(QtGui.QColor("#FAF9F5"))

    # Shift configured as multi-select modifier
    popup_shift = PinShotPopup(
        cropped_pixmap=shot,
        state_dir=tmp_path,
        copy_clipboard=False,
        config={"multi_select_modifier": "shift"},
    )
    popup_shift.show()
    app.processEvents()
    img_rect = popup_shift.canvas.image_rect()

    QtTest.QTest.mouseClick(popup_shift.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, img_rect.topLeft() + QtCore.QPoint(80, 80))
    # Alt+Click should NOT group when modifier is 'shift'; it places standalone pin 2
    QtTest.QTest.mouseClick(popup_shift.canvas, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, img_rect.topLeft() + QtCore.QPoint(160, 80))
    assert len(popup_shift.session.threads) == 2
    # Shift+Click DOES group pin 2 -> 2.1, 2.2
    QtTest.QTest.mouseClick(popup_shift.canvas, QtCore.Qt.LeftButton, QtCore.Qt.ShiftModifier, img_rect.topLeft() + QtCore.QPoint(240, 80))
    assert len(popup_shift.session.threads) == 2
    assert popup_shift.session.threads[1].labels == ["2.1", "2.2"]
    popup_shift.close()

    # Ctrl configured as multi-select modifier
    popup_ctrl = PinShotPopup(
        cropped_pixmap=shot,
        state_dir=tmp_path,
        copy_clipboard=False,
        config={"multi_select_modifier": "ctrl"},
    )
    popup_ctrl.show()
    app.processEvents()
    img_rect_c = popup_ctrl.canvas.image_rect()
    QtTest.QTest.mouseClick(popup_ctrl.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, img_rect_c.topLeft() + QtCore.QPoint(90, 90))
    QtTest.QTest.mouseClick(popup_ctrl.canvas, QtCore.Qt.LeftButton, QtCore.Qt.ControlModifier, img_rect_c.topLeft() + QtCore.QPoint(180, 90))
    assert len(popup_ctrl.session.threads) == 1
    assert popup_ctrl.session.threads[0].labels == ["1.1", "1.2"]
    popup_ctrl.close()


def test_config_roundtrip_normalize_and_history_prune(tmp_path: Path) -> None:
    from dx_pinshot.clipboard import export_session
    from dx_pinshot.config import DEFAULT_CONFIG, load_config, normalize_config, save_config, set_config_value

    cfg_dir = tmp_path / "cfg"
    assert load_config(cfg_dir) == DEFAULT_CONFIG

    norm = normalize_config({
        "theme": "AUTO",
        "multi_select_modifier": "Control",
        "export_mode": "markdown",
        "pin_scale": 2.4,
        "show_leader_lines": "false",
        "keep_history_count": 2,
    })
    assert norm["theme"] == "auto"
    assert norm["multi_select_modifier"] == "ctrl"
    assert norm["export_mode"] == "markdown"
    assert norm["pin_scale"] == 1.5
    assert norm["show_leader_lines"] is False
    assert norm["keep_history_count"] == 2

    saved_path = save_config(norm, config_dir=cfg_dir)
    assert saved_path.exists()
    assert load_config(cfg_dir) == norm

    updated = set_config_value("multi_select_modifier", "shift", config_dir=cfg_dir)
    assert updated["multi_select_modifier"] == "shift"

    _ensure_qapp()
    img = QtGui.QImage(120, 80, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor("#FAF9F5"))
    res = export_session(
        som_image=img,
        raw_image=img,
        prompt_markdown="- **[1]** (x=10, y=10): check",
        mode="markdown",
        state_dir=tmp_path / "state",
        copy_clipboard=False,
        keep_history=2,
    )
    assert res.latest_md.exists()


def test_tui_captures_and_settings_save(tmp_path: Path) -> None:
    import asyncio
    from dx_pinshot.config import load_config
    from dx_pinshot.tui import PinShotTUI
    from textual.widgets import DataTable, Select, Static, Switch, TabbedContent

    cfg_dir = tmp_path / "cfg"
    state_dir = tmp_path / "state"
    hist_dir = state_dir / "history"
    hist_dir.mkdir(parents=True)

    # Seed one capture artifact pair
    _ensure_qapp()
    sample = QtGui.QImage(160, 90, QtGui.QImage.Format_ARGB32)
    sample.fill(QtGui.QColor("#FAF9F5"))
    sample.save(str(hist_dir / "pinshot_20261115_040000.png"), "PNG")
    (hist_dir / "pinshot_20261115_040000.md").write_text(
        "## DX PinShot Annotations · Set-of-Mark (160×90)\n\n"
        "- **[1.1, 1.2]** ((20,30), (80,30)): Align both headers",
        encoding="utf-8",
    )

    async def _run() -> None:
        tui_app = PinShotTUI(config_dir=cfg_dir, state_dir=state_dir, sync_hotkey_on_save=False)
        async with tui_app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            table = tui_app.query_one("#history-table", DataTable)
            assert table.row_count == 1
            preview = str(tui_app.query_one("#prompt-preview", Static).render())
            assert "Align both headers" in preview

            tabs = tui_app.query_one("#tabs", TabbedContent)
            tabs.active = "settings"
            await pilot.pause()

            tui_app.query_one("#cfg-theme", Select).value = "obsidian"
            tui_app.query_one("#cfg-modifier", Select).value = "shift"
            tui_app.query_one("#cfg-export-mode", Select).value = "markdown"
            tui_app.query_one("#cfg-pin-scale", Select).value = "1.2"
            tui_app.query_one("#cfg-leader-lines", Switch).value = False
            await pilot.pause()

            await pilot.click("#btn-save")
            await pilot.pause()

            saved = load_config(cfg_dir)
            assert saved["theme"] == "obsidian"
            assert saved["multi_select_modifier"] == "shift"
            assert saved["export_mode"] == "markdown"
            assert saved["pin_scale"] == 1.2
            assert saved["show_leader_lines"] is False

    asyncio.run(_run())


def test_post_comment_enter_alt_click_promotes_single_pin(tmp_path: Path) -> None:
    app = _ensure_qapp()
    shot = QtGui.QPixmap(1235, 306)
    shot.fill(QtGui.QColor("#FAF9F5"))

    popup = PinShotPopup(cropped_pixmap=shot, state_dir=tmp_path, copy_clipboard=False)
    popup.show()
    app.processEvents()

    img_rect = popup.canvas.image_rect()
    p1 = img_rect.topLeft() + QtCore.QPoint(100, 100)
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, p1)
    app.processEvents()

    # On narrow crops (1235x306), the comment bubble sits cleanly in the margin outside the screenshot
    bubble_in_canvas = QtCore.QRect(popup.canvas.mapFromParent(popup.bubble.pos()), popup.bubble.size())
    assert not bubble_in_canvas.intersects(img_rect)

    popup.bubble.editor.setPlainText("这个箭头和右边不搭配")
    QtTest.QTest.keyClick(popup.bubble.editor, QtCore.Qt.Key_Return)
    app.processEvents()
    assert popup.session.active_thread is None

    # After dictating/typing comment and pressing Enter, holding Alt+Click promotes [1] -> [1.1, 1.2]
    p2 = img_rect.topLeft() + QtCore.QPoint(500, 100)
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, p2)
    app.processEvents()

    assert len(popup.session.threads) == 1
    assert popup.session.threads[0].labels == ["1.1", "1.2"]
    assert popup.session.threads[0].comment == "这个箭头和右边不搭配"
    popup.close()


def test_sticky_multi_select_buttons_and_meta_modifier(tmp_path: Path) -> None:
    app = _ensure_qapp()
    shot = QtGui.QPixmap(800, 480)
    shot.fill(QtGui.QColor("#FAF9F5"))

    popup = PinShotPopup(cropped_pixmap=shot, state_dir=tmp_path, copy_clipboard=False)
    popup.show()
    app.processEvents()

    img_rect = popup.canvas.image_rect()
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, img_rect.topLeft() + QtCore.QPoint(100, 120))
    app.processEvents()
    assert popup.session.threads[0].labels == ["1"]

    # Click `+ 多选加点 (Alt)` in the bubble -> plain clicks add 1.2 and 1.3 without holding Alt
    popup.bubble.multi_btn.click()
    app.processEvents()
    assert popup.multi_mode_btn.isChecked()
    assert popup.bubble.multi_btn.isChecked()

    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, img_rect.topLeft() + QtCore.QPoint(220, 120))
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, img_rect.topLeft() + QtCore.QPoint(340, 120))
    app.processEvents()
    assert len(popup.session.threads) == 1
    assert popup.session.threads[0].labels == ["1.1", "1.2", "1.3"]

    # Pressing Enter commits the group and automatically resets sticky multi-select
    QtTest.QTest.keyClick(popup.bubble.editor, QtCore.Qt.Key_Return)
    app.processEvents()
    assert not popup.multi_mode_btn.isChecked()
    assert not popup.bubble.multi_btn.isChecked()

    # Clicking top header multi-mode toggle + Mac-mode MetaModifier also groups [2.1, 2.2, 2.3]
    popup.multi_mode_btn.click()
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, img_rect.topLeft() + QtCore.QPoint(120, 260))
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, img_rect.topLeft() + QtCore.QPoint(240, 260))
    app.processEvents()
    assert len(popup.session.threads) == 2
    assert popup.session.threads[1].labels == ["2.1", "2.2"]

    popup.multi_mode_btn.click()
    app.processEvents()
    assert not popup.multi_mode_btn.isChecked()
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.MetaModifier, img_rect.topLeft() + QtCore.QPoint(360, 260))
    app.processEvents()
    assert len(popup.session.threads) == 2
    assert popup.session.threads[1].labels == ["2.1", "2.2", "2.3"]
    popup.close()


def test_default_export_mode_is_both_sequential_and_header_copy_buttons(tmp_path: Path, monkeypatch) -> None:
    from dx_pinshot import clipboard as clip_mod
    from dx_pinshot.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["export_mode"] == "both"

    calls: list[tuple[str, bool, bool]] = []
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    monkeypatch.setattr(clip_mod.time, "sleep", lambda _s: None)

    def fake_single(image_path: Path | None, text_path: Path | None, mode: str) -> str:
        calls.append((mode, image_path is not None, text_path is not None))
        return f"x11:xclip({'image/png' if mode == 'image' else 'text/plain'})"

    monkeypatch.setattr(clip_mod, "_copy_single_clipboard", fake_single)

    img = QtGui.QImage(64, 64, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor("#FAF9F5"))
    res = clip_mod.export_session(
        som_image=img,
        raw_image=img,
        prompt_markdown="1. **[1]** test comment",
        mode="both",
        state_dir=tmp_path,
        copy_clipboard=True,
    )
    assert calls == [("prompt", False, True), ("image", True, False)]
    assert res.clipboard_status == "x11:xclip(text/plain)+x11:xclip(image/png)"
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    app = _ensure_qapp()
    shot = QtGui.QPixmap(640, 360)
    shot.fill(QtGui.QColor("#FAF9F5"))

    popup = PinShotPopup(cropped_pixmap=shot, state_dir=tmp_path, copy_clipboard=False)
    popup.show()
    app.processEvents()

    img_rect = popup.canvas.image_rect()
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, img_rect.topLeft() + QtCore.QPoint(80, 80))
    app.processEvents()
    popup.bubble.editor.setPlainText("检查按钮导出")
    QtTest.QTest.keyClick(popup.bubble.editor, QtCore.Qt.Key_Return)
    app.processEvents()

    popup.copy_img_btn.click()
    app.processEvents()
    assert popup.toast_lbl.text() == "已复制图片"
    assert (tmp_path / "latest.png").exists()

    popup.copy_md_btn.click()
    app.processEvents()
    assert popup.toast_lbl.text() == "已复制指令"
    assert (tmp_path / "latest.md").exists()
    popup.close()


def test_fullscreen_toggle_and_open_in_browser(tmp_path: Path, monkeypatch) -> None:
    from dx_pinshot import clipboard as clip_mod

    opened_uris: list[str] = []
    monkeypatch.setattr(clip_mod.webbrowser, "open", lambda uri, new=2: opened_uris.append(uri) or True)

    app = _ensure_qapp()
    shot = QtGui.QPixmap(800, 450)
    shot.fill(QtGui.QColor("#FAF9F5"))

    popup = PinShotPopup(cropped_pixmap=shot, state_dir=tmp_path, copy_clipboard=False)
    popup.show()
    app.processEvents()

    normal_size = popup.size()
    assert popup.fullscreen_btn.text() == "全屏"
    popup.fullscreen_btn.click()
    app.processEvents()
    assert popup._is_fullscreen is True
    assert popup.fullscreen_btn.text() == "还原"

    popup.fullscreen_btn.click()
    app.processEvents()
    assert popup._is_fullscreen is False
    assert popup.fullscreen_btn.text() == "全屏"
    assert popup.size() == normal_size

    img_rect = popup.canvas.image_rect()
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, img_rect.topLeft() + QtCore.QPoint(90, 90))
    app.processEvents()
    popup.bubble.editor.setPlainText("在外部浏览器查看 <tag>")
    QtTest.QTest.keyClick(popup.bubble.editor, QtCore.Qt.Key_Return)
    app.processEvents()

    html_path = popup.open_in_browser()
    app.processEvents()
    assert html_path is not None and html_path.exists()
    content = html_path.read_text(encoding="utf-8")
    assert "data:image/png;base64," in content
    assert "在外部浏览器查看 <tag>" in content
    assert 'id="som-canvas"' in content
    assert 'id="btn-multi"' in content
    assert 'id="sel-pin-theme"' in content
    assert 'id="thread-list"' in content
    assert popup.toast_lbl.text() == "已在网页打开"

    # Verify export_html_preview triggers browser open when not offscreen
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    clip_mod.export_html_preview(
        som_image=popup.build_som_image(),
        raw_image=popup._cropped_raw_qimage(),
        prompt_markdown="1. test",
        state_dir=tmp_path,
        open_browser=True,
    )
    assert len(opened_uris) == 1
    assert opened_uris[0].endswith("latest.html")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    popup.close()


def test_translucent_pin_themes_and_tui_pin_theme_setting(tmp_path: Path) -> None:
    import asyncio
    from textual.widgets import Select
    from dx_pinshot.config import DEFAULT_CONFIG, load_config
    from dx_pinshot.model import AnnotationSession, compute_pin_badge_geometry, render_som_image
    from dx_pinshot.tokens import get_pin_theme
    from dx_pinshot.tui import PinShotTUI

    assert DEFAULT_CONFIG["pin_theme"] == "translucent-ink"
    pt = get_pin_theme("translucent-ink")
    assert 0 < pt.fill_rgba[3] < 255

    _ensure_qapp()
    _pt, rect, _stem = compute_pin_badge_geometry(120, 120, "1", 640, 480, scale=1.0)
    assert rect.height() >= 24.0
    assert rect.width() >= 28.0

    session = AnnotationSession(200, 200)
    session.add_click(80, 80)
    base_img = QtGui.QImage(200, 200, QtGui.QImage.Format_ARGB32)
    base_img.fill(QtGui.QColor("#FFFFFF"))
    img_v = render_som_image(base_img, session, pin_theme="vermilion")
    img_c = render_som_image(base_img, session, pin_theme="cobalt")
    assert img_v != img_c

    cfg_dir = tmp_path / "cfg"
    state_dir = tmp_path / "state"

    async def _run() -> None:
        tui_app = PinShotTUI(config_dir=cfg_dir, state_dir=state_dir, sync_hotkey_on_save=False)
        async with tui_app.run_test() as pilot:
            tui_app.query_one("#cfg-pin-theme", Select).value = "vermilion"
            tui_app.action_save_settings()
            await pilot.pause()
            saved = load_config(cfg_dir)
            assert saved["pin_theme"] == "vermilion"

    asyncio.run(_run())


def test_paste_latest_both_sequence(tmp_path: Path, monkeypatch) -> None:
    from dx_pinshot import clipboard as clip_mod

    (tmp_path / "latest.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (tmp_path / "latest.md").write_text("1. **[1]** paste both test\n", encoding="utf-8")

    events: list[tuple[str, object]] = []
    monkeypatch.setattr(clip_mod.shutil, "which", lambda name: "/usr/bin/xdotool" if name == "xdotool" else None)
    monkeypatch.setattr(clip_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        clip_mod,
        "_copy_single_clipboard",
        lambda img_p, txt_p, mode: events.append(("copy", mode)) or mode,
    )
    monkeypatch.setattr(
        clip_mod.subprocess,
        "run",
        lambda cmd, **kw: events.append(("xdotool", cmd[1:])) or None,
    )

    res = clip_mod.paste_latest_both(state_dir=tmp_path, delay_sec=0.3)
    assert res == {"ok": True, "pasted_image": True, "pasted_markdown": True}
    assert events == [
        ("xdotool", ["keyup", "Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R", "Shift_L", "Shift_R"]),
        ("copy", "image"),
        ("xdotool", ["key", "--clearmodifiers", "ctrl+v"]),
        ("copy", "prompt"),
        ("xdotool", ["key", "--clearmodifiers", "ctrl+v"]),
    ]


def test_drag_move_pin_and_right_click_delete_single_pin(tmp_path: Path) -> None:
    app = _ensure_qapp()
    shot = QtGui.QPixmap(800, 480)
    shot.fill(QtGui.QColor("#FAF9F5"))

    popup = PinShotPopup(cropped_pixmap=shot, state_dir=tmp_path, copy_clipboard=False)
    popup.show()
    app.processEvents()

    img_rect = popup.canvas.image_rect()
    p1 = img_rect.topLeft() + QtCore.QPoint(120, 140)
    p2 = img_rect.topLeft() + QtCore.QPoint(280, 140)
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, p1)
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, p2)
    app.processEvents()

    assert len(popup.session.threads) == 1
    assert popup.session.threads[0].labels == ["1.1", "1.2"]
    pin2_id = popup.session.threads[0].pins[1].id
    old_x = popup.session.threads[0].pins[1].x

    # Drag pin 1.2 to a new position
    badge2_center = popup.canvas._pin_hit_map[pin2_id][1].center().toPoint()
    drag_target = badge2_center + QtCore.QPoint(60, 40)
    QtTest.QTest.mousePress(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, badge2_center)
    move_ev = QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove,
        QtCore.QPointF(drag_target),
        QtCore.Qt.NoButton,
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(popup.canvas, move_ev)
    QtTest.QTest.mouseRelease(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, drag_target)
    app.processEvents()

    assert len(popup.session.threads) == 1
    assert popup.session.threads[0].labels == ["1.1", "1.2"]
    assert popup.session.threads[0].pins[1].x != old_x

    # Right-click pin 1.2 -> deletes ONLY pin 1.2, promoting remaining pin back to "1"
    badge2_after = popup.canvas._pin_hit_map[pin2_id][1].center().toPoint()
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.RightButton, QtCore.Qt.NoModifier, badge2_after)
    app.processEvents()

    assert len(popup.session.threads) == 1
    assert popup.session.threads[0].labels == ["1"]
    assert popup.toast_lbl.text() == "已删除该标点"
    popup.close()


def test_favorites_collect_export_md_pdf_and_batch_paste(tmp_path: Path, monkeypatch) -> None:
    from dx_pinshot import favorites as fav_mod

    app = _ensure_qapp()
    shot = QtGui.QPixmap(640, 360)
    shot.fill(QtGui.QColor("#FAF9F5"))

    popup = PinShotPopup(cropped_pixmap=shot, state_dir=tmp_path, copy_clipboard=False)
    popup.show()
    app.processEvents()

    img_rect = popup.canvas.image_rect()
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, img_rect.topLeft() + QtCore.QPoint(90, 90))
    app.processEvents()
    popup.bubble.editor.setPlainText("收藏第一条标注")
    QtTest.QTest.keyClick(popup.bubble.editor, QtCore.Qt.Key_Return)
    app.processEvents()

    popup.fav_btn.click()
    app.processEvents()
    assert popup.toast_lbl.text() == "已收藏 (共 1 条)"

    # Add a second favorite entry
    QtTest.QTest.mouseClick(popup.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, img_rect.topLeft() + QtCore.QPoint(220, 140))
    app.processEvents()
    popup.bubble.editor.setPlainText("收藏第二条标注")
    QtTest.QTest.keyClick(popup.bubble.editor, QtCore.Qt.Key_Return)
    app.processEvents()
    popup.fav_btn.click()
    app.processEvents()

    items = fav_mod.load_favorites(tmp_path)
    assert len(items) == 2

    md_path = fav_mod.export_favorites_markdown(state_dir=tmp_path, copy_clipboard=False)
    assert md_path.exists()
    md_text = md_path.read_text(encoding="utf-8")
    assert "收藏第一条标注" in md_text and "收藏第二条标注" in md_text

    pdf_path = fav_mod.export_favorites_pdf(state_dir=tmp_path)
    assert pdf_path.exists()
    assert pdf_path.read_bytes().startswith(b"%PDF-")

    events: list[str] = []
    monkeypatch.setattr(fav_mod.shutil, "which", lambda name: "/usr/bin/xdotool" if name == "xdotool" else None)
    monkeypatch.setattr(fav_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(fav_mod, "_copy_single_clipboard", lambda _i, _t, mode: events.append(mode) or mode)
    monkeypatch.setattr(fav_mod.subprocess, "run", lambda cmd, **kw: None)

    res = fav_mod.paste_favorites_bundle(state_dir=tmp_path)
    assert res["ok"] is True
    assert res["pasted_images"] == 2
    assert events == ["image", "image", "prompt"]

    assert fav_mod.clear_favorites(tmp_path) == 2
    assert fav_mod.load_favorites(tmp_path) == []
    popup.close()
