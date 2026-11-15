"""Minimalist Textual TUI for DX PinShot (Captures history, Prompt preview, and Settings)."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from .config import (
    DEFAULT_CONFIG,
    detect_system_theme_name,
    get_config_dir,
    get_state_dir,
    load_config,
    normalize_config,
    save_config,
)
from .hotkey import get_hotkey_status, install_hotkey, uninstall_hotkey

DX_PAPER_THEME = Theme(
    name="dx-paper",
    primary="#191817",
    secondary="#575551",
    warning="#B89B67",
    error="#9E2A2B",
    success="#2D6A4F",
    accent="#B89B67",
    foreground="#191817",
    background="#FAF9F5",
    surface="#FFFFFF",
    panel="#F3F1EA",
    dark=False,
)

DX_OBSIDIAN_THEME = Theme(
    name="dx-obsidian",
    primary="#ECEAE4",
    secondary="#A3A098",
    warning="#C4A46C",
    error="#E07A7A",
    success="#74C69D",
    accent="#9E8558",
    foreground="#ECEAE4",
    background="#121211",
    surface="#1A1A18",
    panel="#21201E",
    dark=True,
)

PIN_SCALE_OPTIONS: list[tuple[str, str]] = [
    ("0.85x · Compact", "0.85"),
    ("1.0x · Standard", "1.0"),
    ("1.2x · Large", "1.2"),
    ("1.4x · Extra Large", "1.4"),
]


@dataclass
class CaptureRecord:
    key: str
    timestamp: str
    pin_count: int
    summary: str
    png_path: Path | None
    md_path: Path | None
    markdown: str


def default_gui_command() -> str:
    package_root = Path(__file__).resolve().parents[2]
    bin_path = package_root / "bin" / "dx-pinshot"
    if bin_path.exists():
        return f"{bin_path} gui"
    return f"{sys.executable} -m dx_pinshot gui"


def resolve_dx_textual_theme(theme_pref: str | None = None) -> str:
    """Resolve Textual theme name matching `dx tui.engine show` or system GNOME preference."""
    pref = (theme_pref or "").strip().lower()
    if pref == "obsidian":
        return DX_OBSIDIAN_THEME.name
    if pref == "paper":
        return DX_PAPER_THEME.name

    try:
        proc = subprocess.run(
            ["dx", "tui.engine", "show"],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        if proc.returncode == 0:
            out = proc.stdout.lower()
            if "obsidian" in out or "dark" in out:
                return DX_OBSIDIAN_THEME.name
            if "paper" in out or "light" in out:
                return DX_PAPER_THEME.name
    except (OSError, subprocess.SubprocessError):
        pass

    return DX_OBSIDIAN_THEME.name if detect_system_theme_name() == "obsidian" else DX_PAPER_THEME.name


def load_capture_records(state_dir: Path) -> list[CaptureRecord]:
    """Scan `<state_dir>/history` (plus `latest.*`) for saved SoM captures and prompts."""
    records: list[CaptureRecord] = []
    history_dir = state_dir / "history"
    if history_dir.exists():
        for png_path in sorted(history_dir.glob("pinshot_*.png"), reverse=True):
            md_path = png_path.with_suffix(".md")
            md_text = md_path.read_text(encoding="utf-8").strip() if md_path.exists() else ""
            stamp = png_path.stem.removeprefix("pinshot_")
            pretty_time = (
                f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}"
                if len(stamp) >= 15 and "_" in stamp
                else stamp
            )
            pins = re.findall(r"^- \*\*\[([^\]]+)\]\*\*", md_text, flags=re.MULTILINE)
            first_line = next(
                (ln.strip() for ln in md_text.splitlines() if ln.strip().startswith("- **[")),
                "(no pins)",
            )
            summary = re.sub(r"\s+", " ", first_line)[:68]
            records.append(
                CaptureRecord(
                    key=png_path.name,
                    timestamp=pretty_time,
                    pin_count=len(pins),
                    summary=summary,
                    png_path=png_path,
                    md_path=md_path if md_path.exists() else None,
                    markdown=md_text,
                )
            )

    if not records:
        latest_png = state_dir / "latest.png"
        latest_md = state_dir / "latest.md"
        if latest_png.exists() or latest_md.exists():
            md_text = latest_md.read_text(encoding="utf-8").strip() if latest_md.exists() else ""
            pins = re.findall(r"^- \*\*\[([^\]]+)\]\*\*", md_text, flags=re.MULTILINE)
            first_line = next(
                (ln.strip() for ln in md_text.splitlines() if ln.strip().startswith("- **[")),
                "(latest capture)",
            )
            records.append(
                CaptureRecord(
                    key="latest",
                    timestamp="Latest",
                    pin_count=len(pins),
                    summary=re.sub(r"\s+", " ", first_line)[:68],
                    png_path=latest_png if latest_png.exists() else None,
                    md_path=latest_md if latest_md.exists() else None,
                    markdown=md_text,
                )
            )

    return records


class PinShotTUI(App[None]):
    """DX PinShot interactive Textual console aligned with dx-win / dx-clip."""

    TITLE = "DX PinShot"
    SUB_TITLE = "Set-of-Mark Screenshot & Grouped Pin Settings"

    CSS = """
    Screen {
        background: $background;
        color: $foreground;
    }

    TabbedContent {
        height: 1fr;
    }

    #status-banner {
        height: auto;
        padding: 1 2;
        background: $panel;
        border: solid $primary 30%;
        margin: 0 0 1 0;
        align: left middle;
    }

    #status-text {
        width: 1fr;
        color: $foreground;
    }

    .top-btn {
        margin-left: 1;
        min-width: 14;
    }

    #captures-split {
        height: 1fr;
    }

    #history-table {
        width: 52%;
        height: 1fr;
        border: solid $primary 25%;
        background: $surface;
        margin-right: 1;
    }

    #preview-pane {
        width: 48%;
        height: 1fr;
        border: solid $primary 25%;
        background: $surface;
        padding: 1 2;
    }

    #preview-header {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #prompt-preview {
        color: $foreground;
    }

    #settings-form {
        height: 1fr;
        border: solid $primary 25%;
        background: $surface;
        padding: 1 2;
        overflow-y: auto;
    }

    .setting-row {
        height: auto;
        margin-bottom: 0;
        align: left middle;
    }

    .setting-label {
        width: 30;
        color: $foreground;
        padding-top: 1;
    }

    .setting-control {
        width: 48;
    }

    #settings-actions {
        height: auto;
        padding: 1 0;
        align: left middle;
    }

    #settings-actions Button {
        margin-right: 1;
    }

    #hotkey-status-label {
        margin-top: 1;
        color: $secondary;
    }

    #settings-feedback {
        margin-top: 1;
        color: $success;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_all", "Refresh"),
        Binding("ctrl+s", "save_settings", "Save Settings"),
    ]

    def __init__(
        self,
        config_dir: Path | str | None = None,
        state_dir: Path | str | None = None,
        sync_hotkey_on_save: bool = True,
        initial_tab: str = "captures",
    ) -> None:
        super().__init__()
        self._config_dir = get_config_dir(config_dir)
        self._state_dir = get_state_dir(state_dir)
        self._sync_hotkey_on_save = sync_hotkey_on_save
        self._initial_tab = initial_tab
        self._config: dict[str, Any] = load_config(self._config_dir)
        self._records: dict[str, CaptureRecord] = {}
        self._selected_key: str | None = None

    def _pin_scale_options(self, current_scale: float) -> list[tuple[str, str]]:
        opts = list(PIN_SCALE_OPTIONS)
        cur_str = str(round(float(current_scale), 2))
        if cur_str.endswith(".00"):
            cur_str = cur_str[:-1]
        values = {v for _lbl, v in opts}
        if cur_str not in values:
            opts.append((f"{cur_str}x · Custom", cur_str))
        return opts

    def compose(self) -> ComposeResult:
        cfg = self._config
        cur_scale_val = str(round(float(cfg["pin_scale"]), 2))
        if cur_scale_val.endswith(".00"):
            cur_scale_val = cur_scale_val[:-1]

        yield Header(show_clock=False)
        with TabbedContent(initial=self._initial_tab, id="tabs"):
            with TabPane("Captures", id="captures"):
                with Horizontal(id="status-banner"):
                    yield Static("Loading DX PinShot status…", id="status-text")
                    yield Button("Launch Capture", id="btn-capture", classes="top-btn", variant="primary")
                    yield Button("Copy Prompt", id="btn-copy-prompt", classes="top-btn")
                    yield Button("Refresh", id="btn-refresh", classes="top-btn")
                with Horizontal(id="captures-split"):
                    yield DataTable(id="history-table")
                    with VerticalScroll(id="preview-pane"):
                        yield Static("Prompt Preview", id="preview-header")
                        yield Static("Select a capture row to inspect its Markdown instruction.", id="prompt-preview")

            with TabPane("Settings", id="settings"):
                with VerticalScroll(id="settings-form"):
                    with Horizontal(classes="setting-row"):
                        yield Label("Theme", classes="setting-label")
                        yield Select(
                            [
                                ("Paper (DX Light · #FAF9F5)", "paper"),
                                ("Obsidian (DX Dark · #121211)", "obsidian"),
                                ("Auto (Follow GNOME System)", "auto"),
                            ],
                            value=str(cfg["theme"]),
                            allow_blank=False,
                            id="cfg-theme",
                            classes="setting-control",
                        )

                    with Horizontal(classes="setting-row"):
                        yield Label("Pin Marker Theme", classes="setting-label")
                        yield Select(
                            [
                                ("Translucent Ink (半透明墨黑)", "translucent-ink"),
                                ("Translucent Paper (半透明纸白)", "translucent-paper"),
                                ("Vermilion SoM (半透明朱砂红)", "vermilion"),
                                ("Cobalt Blue (半透明钴蓝)", "cobalt"),
                                ("Auto (Follow UI Theme)", "auto"),
                            ],
                            value=str(cfg.get("pin_theme", "translucent-ink")),
                            allow_blank=False,
                            id="cfg-pin-theme",
                            classes="setting-control",
                        )

                    with Horizontal(classes="setting-row"):
                        yield Label("Multi-Select Modifier", classes="setting-label")
                        yield Select(
                            [
                                ("Alt (Alt + Click -> 1.1, 1.2…)", "alt"),
                                ("Shift (Shift + Click -> 1.1, 1.2…)", "shift"),
                                ("Ctrl (Ctrl + Click -> 1.1, 1.2…)", "ctrl"),
                            ],
                            value=str(cfg["multi_select_modifier"]),
                            allow_blank=False,
                            id="cfg-modifier",
                            classes="setting-control",
                        )

                    with Horizontal(classes="setting-row"):
                        yield Label("Default Export Mode", classes="setting-label")
                        yield Select(
                            [
                                ("SoM PNG + Markdown Prompt", "both"),
                                ("SoM PNG Image Only", "image"),
                                ("Markdown Prompt Only", "markdown"),
                            ],
                            value=str(cfg["export_mode"]),
                            allow_blank=False,
                            id="cfg-export-mode",
                            classes="setting-control",
                        )

                    with Horizontal(classes="setting-row"):
                        yield Label("Pin Badge Scale", classes="setting-label")
                        yield Select(
                            self._pin_scale_options(float(cfg["pin_scale"])),
                            value=cur_scale_val,
                            allow_blank=False,
                            id="cfg-pin-scale",
                            classes="setting-control",
                        )

                    with Horizontal(classes="setting-row"):
                        yield Label("Show Active Leader Lines", classes="setting-label")
                        yield Switch(value=bool(cfg["show_leader_lines"]), id="cfg-leader-lines")

                    with Horizontal(classes="setting-row"):
                        yield Label("Capture Hotkey", classes="setting-label")
                        yield Input(
                            value=str(cfg["capture_hotkey"]),
                            placeholder="<Primary><Alt>a",
                            id="cfg-hotkey",
                            classes="setting-control",
                        )

                    with Horizontal(classes="setting-row"):
                        yield Label("History Retention Count", classes="setting-label")
                        yield Input(
                            value=str(cfg["keep_history_count"]),
                            placeholder="20",
                            id="cfg-keep-history",
                            classes="setting-control",
                        )

                    with Horizontal(id="settings-actions"):
                        yield Button("Save Settings", id="btn-save", variant="primary")
                        yield Button("Bind Hotkey", id="btn-bind-hotkey")
                        yield Button("Restore Flameshot", id="btn-restore-hotkey")

                    yield Label("", id="hotkey-status-label")
                    yield Label("", id="settings-feedback")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(DX_PAPER_THEME)
        self.register_theme(DX_OBSIDIAN_THEME)
        self.theme = resolve_dx_textual_theme(self._config.get("theme"))

        table = self.query_one("#history-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Timestamp", "Pins", "First Annotation")

        self._refresh_captures()
        self._refresh_hotkey_status()

    def _backup_path(self) -> Path:
        return self._state_dir / "hotkey_backup.json"

    def _safe_hotkey_status(self) -> dict[str, Any]:
        binding = str(self._config.get("capture_hotkey", DEFAULT_CONFIG["capture_hotkey"]))
        try:
            return get_hotkey_status(
                target_command=default_gui_command(),
                binding=binding,
                backup_path=self._backup_path(),
            )
        except Exception as exc:  # pragma: no cover - headless fallback without gsettings daemon
            return {
                "binding": binding,
                "active_is_dx_pinshot": False,
                "command": "",
                "slot_path": None,
                "backup_exists": self._backup_path().exists(),
                "error": str(exc),
            }

    def _refresh_hotkey_status(self) -> None:
        info = self._safe_hotkey_status()
        binding = info.get("binding", "<Primary><Shift>a")
        active = info.get("active_is_dx_pinshot", False)
        slot = info.get("slot_path") or "unbound"
        cmd = info.get("command") or "(none)"
        state_word = "DX PinShot ACTIVE" if active else f"Other ({cmd})"
        banner = (
            f"Hotkey [{binding}]: {state_word} · Modifier: {self._config['multi_select_modifier'].upper()}+Click · "
            f"Export: {self._config['export_mode']}"
        )
        self.query_one("#status-text", Static).update(banner)
        backup_note = "backup saved" if info.get("backup_exists") else "no backup yet"
        self.query_one("#hotkey-status-label", Label).update(
            f"GNOME Shortcut Slot: {slot} | Binding: {binding} | State: {state_word} ({backup_note})"
        )

    def _refresh_captures(self) -> None:
        table = self.query_one("#history-table", DataTable)
        table.clear()
        records = load_capture_records(self._state_dir)
        self._records = {rec.key: rec for rec in records}
        for rec in records:
            table.add_row(rec.timestamp, str(rec.pin_count), rec.summary, key=rec.key)

        if records:
            self._show_record(records[0])
        else:
            self._selected_key = None
            self.query_one("#preview-header", Static).update("Prompt Preview")
            self.query_one("#prompt-preview", Static).update(
                "No captures recorded in state history yet.\n"
                "Press Ctrl+Shift+A or click Launch Capture to annotate a screenshot."
            )

    def _show_record(self, rec: CaptureRecord) -> None:
        self._selected_key = rec.key
        img_str = str(rec.png_path) if rec.png_path else "(no png)"
        self.query_one("#preview-header", Static).update(f"Capture [{rec.timestamp}] · {img_str}")
        self.query_one("#prompt-preview", Static).update(rec.markdown or "(empty markdown prompt)")

    @on(DataTable.RowHighlighted, "#history-table")
    def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row_key = event.row_key.value if event.row_key is not None else None
        if row_key and row_key in self._records:
            self._show_record(self._records[row_key])

    def action_refresh_all(self) -> None:
        self._config = load_config(self._config_dir)
        self._refresh_captures()
        self._refresh_hotkey_status()

    def _collect_settings_from_widgets(self) -> dict[str, Any]:
        theme_val = self.query_one("#cfg-theme", Select).value
        pin_theme_val = self.query_one("#cfg-pin-theme", Select).value
        mod_val = self.query_one("#cfg-modifier", Select).value
        mode_val = self.query_one("#cfg-export-mode", Select).value
        scale_val = self.query_one("#cfg-pin-scale", Select).value
        leader_val = self.query_one("#cfg-leader-lines", Switch).value
        hotkey_val = self.query_one("#cfg-hotkey", Input).value
        keep_val = self.query_one("#cfg-keep-history", Input).value

        return normalize_config(
            {
                "theme": theme_val,
                "pin_theme": pin_theme_val,
                "multi_select_modifier": mod_val,
                "export_mode": mode_val,
                "pin_scale": scale_val,
                "show_leader_lines": leader_val,
                "capture_hotkey": hotkey_val,
                "keep_history_count": keep_val,
            }
        )

    def action_save_settings(self) -> None:
        prev_hotkey = self._config.get("capture_hotkey")
        updated = self._collect_settings_from_widgets()
        path = save_config(updated, config_dir=self._config_dir)
        self._config = updated
        self.theme = resolve_dx_textual_theme(updated["theme"])

        feedback = f"Saved settings -> {path}"
        if self._sync_hotkey_on_save and updated["capture_hotkey"] != prev_hotkey:
            try:
                install_hotkey(
                    target_command=default_gui_command(),
                    binding=str(updated["capture_hotkey"]),
                    backup_path=self._backup_path(),
                )
                feedback += f" (rebound {updated['capture_hotkey']})"
            except Exception as exc:
                feedback += f" (hotkey sync skipped: {exc})"

        self._refresh_hotkey_status()
        self.query_one("#settings-feedback", Label).update(feedback)

    @on(Button.Pressed, "#btn-save")
    def _on_save_pressed(self) -> None:
        self.action_save_settings()

    @on(Button.Pressed, "#btn-bind-hotkey")
    def _on_bind_hotkey_pressed(self) -> None:
        self._config = self._collect_settings_from_widgets()
        save_config(self._config, config_dir=self._config_dir)
        try:
            info = install_hotkey(
                target_command=default_gui_command(),
                binding=str(self._config["capture_hotkey"]),
                backup_path=self._backup_path(),
            )
            msg = f"Bound {info['binding']} -> dx-pinshot gui ({info['slot_path']})"
        except Exception as exc:
            msg = f"Bind failed: {exc}"
        self._refresh_hotkey_status()
        self.query_one("#settings-feedback", Label).update(msg)

    @on(Button.Pressed, "#btn-restore-hotkey")
    def _on_restore_hotkey_pressed(self) -> None:
        try:
            info = uninstall_hotkey(backup_path=self._backup_path())
            msg = f"Restored {info['restored_binding']} -> {info['restored_command']}"
        except Exception as exc:
            msg = f"Restore failed: {exc}"
        self._refresh_hotkey_status()
        self.query_one("#settings-feedback", Label).update(msg)

    @on(Button.Pressed, "#btn-refresh")
    def _on_refresh_pressed(self) -> None:
        self.action_refresh_all()

    @on(Button.Pressed, "#btn-capture")
    def _on_capture_pressed(self) -> None:
        package_root = Path(__file__).resolve().parents[2]
        bin_path = package_root / "bin" / "dx-pinshot"
        cmd = [str(bin_path), "gui"] if bin_path.exists() else [sys.executable, "-m", "dx_pinshot", "gui"]
        env = os.environ.copy()
        try:
            subprocess.Popen(
                cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.query_one("#preview-header", Static).update("Capture launched in background desktop session.")
        except OSError as exc:
            self.query_one("#preview-header", Static).update(f"Failed to launch capture: {exc}")

    @on(Button.Pressed, "#btn-copy-prompt")
    def _on_copy_prompt_pressed(self) -> None:
        if not self._selected_key or self._selected_key not in self._records:
            return
        text = self._records[self._selected_key].markdown
        if not text:
            return
        self.copy_to_clipboard(text)
        self.query_one("#preview-header", Static).update("Copied Markdown prompt to clipboard.")


def run_tui(
    config_dir: Path | str | None = None,
    state_dir: Path | str | None = None,
    initial_tab: str = "captures",
) -> int:
    """Launch DX PinShot Textual UI."""
    app = PinShotTUI(config_dir=config_dir, state_dir=state_dir, initial_tab=initial_tab)
    app.run()
    return 0
