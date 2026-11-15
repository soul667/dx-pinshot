# DX PinShot

> Minimalist Set-of-Mark screenshot & grouped pin annotator in DX Paper / Obsidian editorial style.

**Homepage & Showcase:** https://soul667.github.io/dx-pinshot/

## Core Features

- **Click-to-Pin (`1, 2, 3…`)**: Click any region on a captured screenshot to drop a numbered marker and immediately type or voice-dictate comments (`Enter` to commit).
- **Grouped Multi-Select (`1.1, 1.2, 1.3…`)**: Hold `Alt` (configurable to `Shift` / `Ctrl`) or toggle **「多选」** to attach multiple related spatial pins to one shared comment thread with dotted leader lines.
- **Arrow Pointers, Drag & Single-Pin Delete**: Crisp directional arrows, left-drag any pin to adjust its position, right-click any individual pin (even `1.2` inside a group) to delete only that pin.
- **Translucent Pin Themes**: Choose `translucent-ink`, `translucent-paper`, `vermilion`, `cobalt`, or `auto` in `dx pinshot tui`.
- **Fullscreen & Interactive Web Workbench**: Press `F11` for fullscreen annotation or `Ctrl+O` to open a self-contained browser annotation workbench (`latest.html`) inheriting the active session.
- **Seamless `dx-clip` Integration**:
  - `Ctrl+Alt+A`: Launch DX PinShot (`Ctrl+Shift+A` stays bound to Flameshot).
  - `Ctrl+Alt+1`: One-key dual paste (`dx-pinshot paste-both`) — pastes the Set-of-Mark PNG followed by the Markdown instructions into any focused chat box.

## Install as DX Plugin

```bash
dx plugin install --enable --force /path/to/dx-pinshot
```

## Tests

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src pytest tests/test_dx_pinshot.py -v
```
