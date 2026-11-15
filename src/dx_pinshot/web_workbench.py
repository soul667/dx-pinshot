"""Self-contained interactive HTML Annotation Workbench generator for DX PinShot."""

from __future__ import annotations

import html
import json
from typing import Any

from .tokens import PIN_THEMES, get_pin_theme, get_theme


def _pin_theme_js_map(ui_theme_name: str) -> dict[str, dict[str, str]]:
    ui_theme = get_theme(ui_theme_name)
    out: dict[str, dict[str, str]] = {}
    for key in (*PIN_THEMES.keys(), "auto"):
        pt = get_pin_theme(key, ui_theme=ui_theme)
        r, g, b, a = pt.fill_rgba
        hr, hg, hb, ha = pt.halo_rgba
        out[key] = {
            "fill": f"rgba({r},{g},{b},{a / 255.0:.3f})",
            "text": pt.text_hex,
            "border": pt.border_hex,
            "active": pt.active_hex,
            "halo": f"rgba({hr},{hg},{hb},{ha / 255.0:.3f})",
        }
    return out


def build_workbench_html(
    *,
    raw_png_b64: str,
    crop_width: int,
    crop_height: int,
    threads_data: list[dict[str, Any]],
    theme_name: str = "paper",
    pin_theme: str = "translucent-ink",
    pin_scale: float = 1.0,
    show_leader_lines: bool = True,
) -> str:
    """Render a self-contained DX PinShot interactive annotation workbench HTML document."""
    is_dark = theme_name == "obsidian"
    bg = "#121211" if is_dark else "#FAF9F5"
    panel = "#1A1A18" if is_dark else "#FFFFFF"
    subtle = "#21201E" if is_dark else "#F3F1EA"
    ink = "#ECEAE4" if is_dark else "#191817"
    muted = "#9A968E" if is_dark else "#6F6B64"
    line = "#2B2A27" if is_dark else "#E6E2D8"
    line_strong = "#6A665E" if is_dark else "#9E988E"
    accent = "#D4AF6A" if is_dark else "#B89B67"
    danger = "#E07A7A" if is_dark else "#9E2A2B"

    state_json = json.dumps(
        {
            "cropWidth": max(1, int(crop_width)),
            "cropHeight": max(1, int(crop_height)),
            "threads": threads_data,
            "pinTheme": pin_theme,
            "pinScale": float(pin_scale),
            "showLeaderLines": bool(show_leader_lines),
            "pinThemes": _pin_theme_js_map(theme_name),
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")

    dims = html.escape(f"{crop_width}×{crop_height}")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DX · PinShot 标注工作台</title>
  <style>
    :root {{
      --bg: {bg}; --panel: {panel}; --subtle: {subtle}; --ink: {ink};
      --muted: {muted}; --line: {line}; --line-strong: {line_strong};
      --accent: {accent}; --danger: {danger};
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; margin: 0; background: var(--bg); color: var(--ink); }}
    body {{
      display: flex; flex-direction: column;
      font-family: "Newsreader", "Noto Serif CJK SC", "Source Han Serif SC", Georgia, serif;
    }}
    header {{
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
      padding: 10px 16px; background: var(--panel); border-bottom: 1px solid var(--line);
    }}
    .brand {{ font-weight: 700; font-size: 15px; letter-spacing: 0.3px; }}
    .meta {{
      font-family: "JetBrains Mono", ui-monospace, Menlo, monospace;
      font-size: 12px; color: var(--muted);
    }}
    .spacer {{ flex: 1; }}
    .toolbar {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
    button, select, a.btn {{
      appearance: none; border: 1px solid var(--line); background: transparent; color: var(--ink);
      padding: 5px 10px; font-size: 12px; cursor: pointer; text-decoration: none; border-radius: 0;
      font-family: "JetBrains Mono", ui-monospace, Menlo, monospace;
    }}
    button:hover, select:hover, a.btn:hover {{ border-color: var(--line-strong); }}
    button.active {{ background: var(--subtle); border-color: var(--accent); color: var(--ink); font-weight: 600; }}
    button.danger:hover {{ border-color: var(--danger); color: var(--danger); }}
    button.primary {{ background: var(--ink); color: var(--bg); border-color: var(--ink); font-weight: 600; }}
    button.primary:hover {{ background: var(--accent); border-color: var(--accent); color: #191817; }}
    main {{
      flex: 1; min-height: 0; display: grid;
      grid-template-columns: minmax(0, 1.85fr) minmax(300px, 1fr);
    }}
    @media (max-width: 960px) {{ main {{ grid-template-columns: 1fr; grid-template-rows: 1fr auto; }} }}
    .stage {{
      position: relative; overflow: auto; padding: 20px;
      display: flex; align-items: center; justify-content: center;
      background: var(--bg); border-right: 1px solid var(--line);
    }}
    .canvas-wrap {{ position: relative; display: inline-block; line-height: 0; border: 1px solid var(--line); }}
    #som-canvas {{ display: block; max-width: 100%; height: auto; cursor: crosshair; }}
    .bubble {{
      position: fixed; z-index: 40; width: 300px; background: var(--panel);
      border: 1px solid var(--line-strong); padding: 10px; display: none;
      box-shadow: 0 8px 24px rgba(0,0,0,0.16);
    }}
    .bubble-head {{
      display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px;
      font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 12px;
    }}
    .bubble textarea {{
      width: 100%; min-height: 68px; resize: vertical; border: 1px solid var(--line);
      background: var(--bg); color: var(--ink); padding: 7px 8px; font-size: 13px;
      font-family: inherit; outline: none;
    }}
    .bubble textarea:focus {{ border-color: var(--accent); }}
    .bubble-foot {{ display: flex; justify-content: flex-end; gap: 6px; margin-top: 8px; }}
    .side {{
      display: flex; flex-direction: column; min-height: 0;
      background: var(--panel); overflow: hidden;
    }}
    .side-section {{ padding: 12px 14px; border-bottom: 1px solid var(--line); }}
    .side-title {{
      margin: 0 0 8px 0; font-size: 12px; color: var(--muted);
      font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; text-transform: uppercase;
    }}
    .thread-list {{ flex: 1; overflow-y: auto; padding: 10px 14px; display: flex; flex-direction: column; gap: 8px; }}
    .thread-card {{
      border: 1px solid var(--line); background: var(--bg); padding: 8px 10px; cursor: pointer;
    }}
    .thread-card.active {{ border-color: var(--accent); background: var(--subtle); }}
    .thread-card-head {{
      display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 5px;
      font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 12px;
    }}
    .thread-card textarea {{
      width: 100%; min-height: 44px; border: 1px solid var(--line); background: var(--panel);
      color: var(--ink); padding: 5px 7px; font-size: 12.5px; font-family: inherit; resize: vertical;
    }}
    pre {{
      margin: 0; padding: 10px; max-height: 170px; overflow: auto;
      border: 1px solid var(--line); background: var(--bg);
      white-space: pre-wrap; word-break: break-word; font-size: 11.5px; line-height: 1.5;
      font-family: "JetBrains Mono", ui-monospace, Menlo, monospace;
    }}
    footer {{
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
      padding: 6px 16px; background: var(--panel); border-top: 1px solid var(--line);
      font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 11px; color: var(--muted);
    }}
    #toast {{ color: var(--accent); font-weight: 600; }}
  </style>
</head>
<body>
  <header>
    <span class="brand">DX · PinShot 工作台</span>
    <span class="meta" id="meta-lbl">{dims} · 0 处标注</span>
    <div class="spacer"></div>
    <div class="toolbar">
      <button id="btn-multi" title="开启连续多选 (Alt+Click)">多选 (Alt)</button>
      <select id="sel-pin-theme" title="标记主题">
        <option value="translucent-ink">标记: 半透明墨黑</option>
        <option value="translucent-paper">标记: 半透明纸白</option>
        <option value="vermilion">标记: 半透明朱砂红</option>
        <option value="cobalt">标记: 半透明钴蓝</option>
        <option value="auto">标记: 跟随主题</option>
      </select>
      <button id="btn-undo" title="撤回上一个点 (Ctrl+Z)">撤回</button>
      <button id="btn-clear" class="danger" title="清空所有标注">清空</button>
      <button id="btn-copy-img" title="复制合成后的 Set-of-Mark 图片">复制图片</button>
      <button id="btn-copy-md" title="复制 Markdown 批注指令">复制指令</button>
      <button id="btn-download" class="primary">下载 SoM PNG</button>
    </div>
  </header>
  <main>
    <section class="stage">
      <div class="canvas-wrap">
        <canvas id="som-canvas"></canvas>
      </div>
    </section>
    <aside class="side">
      <div class="side-section">
        <div class="side-title">批注列表（点击画面加点 · 点击列表编辑）</div>
      </div>
      <div class="thread-list" id="thread-list"></div>
      <div class="side-section">
        <div class="side-title">VLM Markdown 指令预览</div>
        <pre id="md-preview"></pre>
      </div>
    </aside>
  </main>
  <div class="bubble" id="bubble">
    <div class="bubble-head">
      <strong id="bubble-badge">[1]</strong>
      <button id="bubble-del" class="danger">删除</button>
    </div>
    <textarea id="bubble-input" placeholder="输入批注（Enter 确认 · Shift+Enter 换行）"></textarea>
    <div class="bubble-foot">
      <button id="bubble-close">收起</button>
      <button id="bubble-ok" class="primary">确认</button>
    </div>
  </div>
  <footer>
    <span>Click 打点 (1, 2…) · Alt+Click /「多选」同组连点 (1.1, 1.2…) · Ctrl+Z 撤回</span>
    <span id="toast"></span>
  </footer>
  <script>
    const INIT = {state_json};
    const img = new Image();
    const canvas = document.getElementById("som-canvas");
    const ctx = canvas.getContext("2d");
    const bubble = document.getElementById("bubble");
    const bubbleBadge = document.getElementById("bubble-badge");
    const bubbleInput = document.getElementById("bubble-input");
    const threadListEl = document.getElementById("thread-list");
    const mdPreviewEl = document.getElementById("md-preview");
    const metaLbl = document.getElementById("meta-lbl");
    const toastEl = document.getElementById("toast");
    const multiBtn = document.getElementById("btn-multi");
    const themeSel = document.getElementById("sel-pin-theme");

    let threads = (INIT.threads || []).map((t, idx) => ({{
      id: t.id || ("t_" + idx + "_" + Date.now()),
      number: idx + 1,
      comment: t.comment || "",
      pins: (t.pins || []).map((p, pidx) => ({{
        id: p.id || ("p_" + pidx + "_" + Date.now()),
        x: Number(p.x) || 0,
        y: Number(p.y) || 0,
      }})),
    }}));
    let activeThreadId = null;
    let stickyMulti = false;
    let pinThemeKey = INIT.pinTheme || "translucent-ink";
    let hitRects = [];
    themeSel.value = pinThemeKey;

    function showToast(msg) {{
      toastEl.textContent = msg;
      clearTimeout(showToast._t);
      showToast._t = setTimeout(() => {{ toastEl.textContent = ""; }}, 2200);
    }}

    function pinLabels(thread) {{
      if (thread.pins.length <= 1) return [String(thread.number)];
      return thread.pins.map((_, i) => thread.number + "." + (i + 1));
    }}

    function renumber() {{
      threads = threads.filter(t => t.pins.length > 0);
      threads.forEach((t, i) => {{ t.number = i + 1; }});
      if (activeThreadId && !threads.some(t => t.id === activeThreadId)) activeThreadId = null;
    }}

    function formatMarkdown() {{
      if (!threads.length) return "(暂无批注)";
      const lines = [
        "## DX PinShot Annotations · Set-of-Mark (" + INIT.cropWidth + "×" + INIT.cropHeight + ")",
        "",
      ];
      for (const t of threads) {{
        const labels = pinLabels(t);
        const tag = labels.length === 1 ? "[" + labels[0] + "]" : "[" + labels.join(", ") + "]";
        const coords = t.pins.map(p => "(" + Math.round(p.x) + "," + Math.round(p.y) + ")").join(", ");
        const body = (t.comment || "").trim() || "(无文字说明)";
        lines.push("- **" + tag + "** (" + coords + "): " + body);
      }}
      return lines.join("\\n");
    }}

    function currentPinTheme() {{
      return (INIT.pinThemes && INIT.pinThemes[pinThemeKey]) || INIT.pinThemes["translucent-ink"];
    }}

    function badgeGeom(px, py, label, w, h, s) {{
      ctx.font = "bold " + (11.5 * s) + "px 'JetBrains Mono', monospace";
      const tw = ctx.measureText(label).width;
      const bw = Math.max(28 * s, Math.ceil(tw + 16 * s));
      const bh = 25 * s;
      const gap = 7 * s;
      let bx = px + gap;
      let by = py - bh - gap;
      if (bx + bw > w - 2) bx = px - bw - gap;
      if (bx < 2) bx = 2;
      if (by < 2) by = py + gap;
      if (by + bh > h - 2) by = Math.max(2, h - bh - 2);
      const sx = Math.max(bx, Math.min(px, bx + bw));
      const sy = Math.max(by, Math.min(py, by + bh));
      return {{ bx, by, bw, bh, sx, sy }};
    }}

    function renderCanvas(highlightActive = true) {{
      if (!img.complete || !img.naturalWidth) return;
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0);

      const s = Math.max(0.75, Math.min(1.5, Number(INIT.pinScale) || 1.0));
      const pt = currentPinTheme();
      hitRects = [];

      for (const t of threads) {{
        const isActive = highlightActive && t.id === activeThreadId;
        const labels = pinLabels(t);

        if (INIT.showLeaderLines && isActive && t.pins.length > 1) {{
          ctx.save();
          ctx.setLineDash([4 * s, 4 * s]);
          ctx.strokeStyle = pt.active;
          ctx.lineWidth = 1.5 * s;
          ctx.beginPath();
          t.pins.forEach((p, idx) => idx === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
          ctx.stroke();
          ctx.restore();
        }}

        t.pins.forEach((p, idx) => {{
          const label = labels[idx];
          const g = badgeGeom(p.x, p.y, label, canvas.width, canvas.height, s);

          // Stem
          ctx.lineWidth = 2.5 * s; ctx.strokeStyle = pt.halo;
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(g.sx, g.sy); ctx.stroke();
          ctx.lineWidth = 1.35 * s; ctx.strokeStyle = isActive ? pt.active : pt.fill;
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(g.sx, g.sy); ctx.stroke();

          // Target dot
          const dr = 3.8 * s;
          ctx.fillStyle = isActive ? pt.active : pt.fill;
          ctx.strokeStyle = pt.halo; ctx.lineWidth = 1.4 * s;
          ctx.fillRect(p.x - dr, p.y - dr, dr * 2, dr * 2);
          ctx.strokeRect(p.x - dr, p.y - dr, dr * 2, dr * 2);

          // Badge halo + translucent fill + border + label
          ctx.strokeStyle = pt.halo; ctx.lineWidth = 2.2 * s;
          ctx.strokeRect(g.bx, g.by, g.bw, g.bh);
          ctx.fillStyle = pt.fill;
          ctx.fillRect(g.bx, g.by, g.bw, g.bh);
          ctx.strokeStyle = (isActive || t.pins.length > 1) ? pt.active : pt.border;
          ctx.lineWidth = 1.3 * s;
          ctx.strokeRect(g.bx, g.by, g.bw, g.bh);

          ctx.fillStyle = pt.text;
          ctx.font = "bold " + (11.5 * s) + "px 'JetBrains Mono', monospace";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(label, g.bx + g.bw / 2, g.by + g.bh / 2 + 0.5);

          hitRects.push({{ threadId: t.id, x1: g.bx - 5, y1: g.by - 5, x2: g.bx + g.bw + 5, y2: g.by + g.bh + 5 }});
        }});
      }}
    }}

    function renderSidebar() {{
      const totalPins = threads.reduce((acc, t) => acc + t.pins.length, 0);
      metaLbl.textContent = INIT.cropWidth + "×" + INIT.cropHeight + " · " + threads.length + " 组 / " + totalPins + " 点";
      mdPreviewEl.textContent = formatMarkdown();
      threadListEl.innerHTML = "";

      for (const t of threads) {{
        const card = document.createElement("div");
        card.className = "thread-card" + (t.id === activeThreadId ? " active" : "");
        const head = document.createElement("div");
        head.className = "thread-card-head";
        const labels = pinLabels(t);
        const tagText = labels.length === 1 ? "[" + labels[0] + "]" : "[" + labels.join(", ") + "]";
        const title = document.createElement("strong");
        title.textContent = tagText;
        const delBtn = document.createElement("button");
        delBtn.className = "danger";
        delBtn.textContent = "删除";
        delBtn.onclick = (ev) => {{
          ev.stopPropagation();
          threads = threads.filter(x => x.id !== t.id);
          renumber();
          syncAll();
        }};
        head.append(title, delBtn);

        const ta = document.createElement("textarea");
        ta.value = t.comment;
        ta.placeholder = "点击输入批注说明...";
        ta.onclick = (ev) => ev.stopPropagation();
        ta.oninput = () => {{
          t.comment = ta.value;
          if (t.id === activeThreadId) bubbleInput.value = t.comment;
          mdPreviewEl.textContent = formatMarkdown();
        }};

        card.onclick = () => {{
          activeThreadId = t.id;
          syncAll();
          openBubbleForThread(t);
        }};
        card.append(head, ta);
        threadListEl.appendChild(card);
      }}
    }}

    function openBubbleForThread(thread, clientX, clientY) {{
      if (!thread || !thread.pins.length) return;
      const labels = pinLabels(thread);
      bubbleBadge.textContent = labels.length === 1 ? "[" + labels[0] + "]" : "[" + labels.join(", ") + "]";
      bubbleInput.value = thread.comment || "";
      const rect = canvas.getBoundingClientRect();
      const lastPin = thread.pins[thread.pins.length - 1];
      const cx = clientX ?? (rect.left + (lastPin.x / canvas.width) * rect.width);
      const cy = clientY ?? (rect.top + (lastPin.y / canvas.height) * rect.height);
      const left = Math.max(12, Math.min(window.innerWidth - 316, cx + 16));
      const top = Math.max(12, Math.min(window.innerHeight - 190, cy + 16));
      bubble.style.left = left + "px";
      bubble.style.top = top + "px";
      bubble.style.display = "block";
      bubbleInput.focus();
    }}

    function hideBubble() {{
      bubble.style.display = "none";
      activeThreadId = null;
      syncAll();
    }}

    function syncAll() {{
      renumber();
      renderCanvas(true);
      renderSidebar();
    }}

    canvas.addEventListener("click", (e) => {{
      const rect = canvas.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * canvas.width;
      const y = ((e.clientY - rect.top) / rect.height) * canvas.height;

      const hit = hitRects.slice().reverse().find(h => x >= h.x1 && x <= h.x2 && y >= h.y1 && y <= h.y2);
      const isMulti = e.altKey || stickyMulti;
      if (hit && !isMulti) {{
        activeThreadId = hit.threadId;
        const t = threads.find(item => item.id === activeThreadId);
        syncAll();
        openBubbleForThread(t, e.clientX, e.clientY);
        return;
      }}

      let target = activeThreadId ? threads.find(t => t.id === activeThreadId) : null;
      if (isMulti && !target && threads.length) target = threads[threads.length - 1];

      if (isMulti && target) {{
        target.pins.push({{ id: "p_" + Date.now(), x, y }});
        activeThreadId = target.id;
      }} else {{
        const created = {{
          id: "t_" + Date.now() + "_" + Math.random().toString(16).slice(2, 6),
          number: threads.length + 1,
          comment: "",
          pins: [{{ id: "p_" + Date.now(), x, y }}],
        }};
        threads.push(created);
        activeThreadId = created.id;
        target = created;
      }}
      syncAll();
      openBubbleForThread(target, e.clientX, e.clientY);
    }});

    bubbleInput.addEventListener("input", () => {{
      const t = threads.find(x => x.id === activeThreadId);
      if (t) {{
        t.comment = bubbleInput.value;
        renderSidebar();
      }}
    }});
    bubbleInput.addEventListener("keydown", (e) => {{
      if (e.key === "Enter" && !e.shiftKey) {{
        e.preventDefault();
        stickyMulti = false;
        multiBtn.classList.remove("active");
        hideBubble();
      }} else if (e.key === "Escape") {{
        e.preventDefault();
        hideBubble();
      }}
    }});
    document.getElementById("bubble-ok").onclick = () => {{
      stickyMulti = false;
      multiBtn.classList.remove("active");
      hideBubble();
    }};
    document.getElementById("bubble-close").onclick = hideBubble;
    document.getElementById("bubble-del").onclick = () => {{
      threads = threads.filter(x => x.id !== activeThreadId);
      hideBubble();
    }};

    multiBtn.onclick = () => {{
      stickyMulti = !stickyMulti;
      multiBtn.classList.toggle("active", stickyMulti);
    }};
    themeSel.onchange = () => {{
      pinThemeKey = themeSel.value;
      renderCanvas(true);
    }};
    document.getElementById("btn-undo").onclick = () => {{
      if (!threads.length) return;
      const last = threads[threads.length - 1];
      last.pins.pop();
      if (!last.pins.length) threads.pop();
      syncAll();
    }};
    document.getElementById("btn-clear").onclick = () => {{
      threads = [];
      hideBubble();
    }};
    document.getElementById("btn-copy-md").onclick = async () => {{
      const md = formatMarkdown();
      try {{
        await navigator.clipboard.writeText(md);
        showToast("已复制指令 ✓");
      }} catch (_err) {{
        showToast("请从右侧指令区选中复制");
      }}
    }};
    document.getElementById("btn-download").onclick = () => {{
      renderCanvas(false);
      const a = document.createElement("a");
      a.download = "pinshot_som.png";
      a.href = canvas.toDataURL("image/png");
      a.click();
      renderCanvas(true);
      showToast("已下载 PNG ✓");
    }};
    document.getElementById("btn-copy-img").onclick = () => {{
      renderCanvas(false);
      canvas.toBlob(async (blob) => {{
        renderCanvas(true);
        if (!blob) return;
        try {{
          await navigator.clipboard.write([new ClipboardItem({{ "image/png": blob }})]);
          showToast("已复制图片 ✓");
        }} catch (_err) {{
          const a = document.createElement("a");
          a.download = "pinshot_som.png";
          a.href = URL.createObjectURL(blob);
          a.click();
          showToast("file:// 受限，已改为下载 PNG");
        }}
      }}, "image/png");
    }};
    window.addEventListener("keydown", (e) => {{
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && document.activeElement !== bubbleInput) {{
        e.preventDefault();
        document.getElementById("btn-undo").click();
      }}
    }});

    img.onload = () => syncAll();
    img.src = "data:image/png;base64,{raw_png_b64}";
  </script>
</body>
</html>
"""
