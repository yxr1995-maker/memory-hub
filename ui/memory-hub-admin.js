/* Memory Hub Admin v0.2.0 — codex++ 用户脚本
 * 从 Codex 左侧边栏进入的 memory-hub 管理面板。
 * 后端：memory-hub serve（scripts/server.py），默认 http://127.0.0.1:8787
 * 覆盖能力：agent 调用日志 / 实时上下文流 / Obsidian 风格图谱 / 页面 CRUD / 标签 / 统计
 */
(() => {
  if (window.top && window.self && window.top !== window.self) return;

  // ============================================================
  // 区域 1/14：常量、配置与幂等重载
  // ============================================================
  const API_KEY = "__memoryHubAdmin";
  const VERSION = "0.2.0";
  const HOST_ID = "memory-hub-admin-host";
  const ENTRY_ATTR = "data-memory-hub-admin-entry";
  const API_BASE = (localStorage.getItem("memoryHubAdmin.apiBase") || "http://127.0.0.1:8787").replace(/\/+$/, "");
  const PAGE_SIZE = 50;
  const SIDEBAR_WAIT_MS = 10000;
  // Obsidian vault 名（可用 localStorage memoryHubAdmin.obsidianVault 覆盖）
  const OBS_VAULT = localStorage.getItem("memoryHubAdmin.obsidianVault") || "llm-wiki";
  const obsOpenUri = (relPath) =>
    `obsidian://open?vault=${encodeURIComponent(OBS_VAULT)}&file=${encodeURIComponent(String(relPath).replace(/\.md$/, ""))}`;

  const prev = window[API_KEY];
  if (prev && typeof prev.destroy === "function") prev.destroy();

  // ============================================================
  // 区域 2/14：工具函数（esc / debounce / 格式化 / toast）
  // ============================================================
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const debounce = (fn, ms) => {
    let t = 0;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  };
  const fmtBytes = (n) => {
    n = Number(n) || 0;
    if (n >= 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
    if (n >= 1 << 10) return (n / (1 << 10)).toFixed(1) + " KB";
    return n + " B";
  };
  const fmtAge = (sec) => {
    if (sec === null || sec === undefined) return "—";
    sec = Number(sec);
    if (sec < 60) return sec + " 秒前";
    if (sec < 3600) return Math.floor(sec / 60) + " 分钟前";
    if (sec < 86400) return Math.floor(sec / 3600) + " 小时前";
    return Math.floor(sec / 86400) + " 天前";
  };
  const fmtDate = (s) => {
    if (!s) return "—";
    const d = new Date(s);
    return isNaN(d) ? String(s).slice(0, 16) : d.toLocaleString("zh-CN", { hour12: false });
  };

  // ============================================================
  // 区域 3/14：API 客户端（超时 / 错误归一化）
  // ============================================================
  async function api(path, opts = {}, timeoutMs = 20000) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch(API_BASE + path, { ...opts, signal: ctl.signal });
      const ct = res.headers.get("content-type") || "";
      const body = ct.includes("json") ? await res.json() : await res.text();
      if (!res.ok) throw new Error(typeof body === "string" ? body : (body.error || `HTTP ${res.status}`));
      return body;
    } catch (e) {
      if (e.name === "AbortError") throw new Error(`请求超时（>${timeoutMs / 1000}s）`);
      if (e instanceof TypeError) throw new Error(`无法连接 memory-hub 服务（${API_BASE}）— 请先运行 memory-hub.sh serve`);
      throw e;
    } finally {
      clearTimeout(t);
    }
  }

  // ============================================================
  // 区域 4/14：frontmatter 解析 / 重建（与服务端同构的迷你实现）
  // ============================================================
  const unquote = (s) => {
    s = String(s).trim();
    if (s.length >= 2 && s[0] === "'" && s.endsWith("'")) return s.slice(1, -1).replace(/''/g, "'");
    if (s.length >= 2 && s[0] === '"' && s.endsWith('"')) return s.slice(1, -1);
    return s;
  };
  function parseMarkdown(text) {
    const m = String(text).match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
    if (!m) return { meta: {}, body: String(text) };
    const meta = {};
    let key = "";
    for (const line of m[1].split(/\r?\n/)) {
      const li = line.match(/^\s+-\s+(.*)$/);
      if (li && key) {
        if (!Array.isArray(meta[key])) meta[key] = [];
        meta[key].push(unquote(li[1]));
        continue;
      }
      const kv = line.match(/^([A-Za-z_][\w-]*):\s*(.*)$/);
      if (kv) {
        key = kv[1];
        const raw = kv[2].trim();
        if (raw === "") {
          meta[key] = "";
        } else if (raw.startsWith("[") && raw.endsWith("]")) {
          // 行内 flow 列表：tags: [] 或 tags: [a, b]
          const inner = raw.slice(1, -1).trim();
          meta[key] = inner ? inner.split(",").map((s) => unquote(s.trim())).filter(Boolean) : [];
        } else {
          meta[key] = unquote(raw);
        }
      }
    }
    return { meta, body: String(text).slice(m[0].length) };
  }
  function yamlScalar(v) {
    const s = String(v ?? "");
    if (s === "") return "''";
    if (/^(true|false|null|~)$/i.test(s)) return s.toLowerCase();
    if (/^-?\d+(\.\d+)?$/.test(s)) return s;
    if (/[:#\[\]{},&*!|>'"%@`]|^\s|\s$|\s/.test(s)) return `'${s.replace(/'/g, "''")}'`;
    return s;
  }
  function buildMarkdown(meta, body) {
    const lines = ["---"];
    for (const [k, v] of Object.entries(meta)) {
      if (Array.isArray(v)) {
        if (!v.length) { lines.push(`${k}: []`); continue; }
        lines.push(`${k}:`);
        for (const item of v) lines.push(`  - ${yamlScalar(item)}`);
      } else {
        lines.push(`${k}: ${yamlScalar(v)}`);
      }
    }
    lines.push("---", "");
    return lines.join("\n") + String(body || "").replace(/^\n+/, "");
  }

  // ============================================================
  // 区域 5/14：样式表（shadow DOM 内，深浅色自适应）
  // ============================================================
  // 设计基调与 Codex 桌面端对齐：单色克制、发丝边框、白/黑主按钮、无渐变发光
  const CSS = `
    :host { all: initial;
      --bg: #0f0f12; --bg2: #17171b; --fg: #ececf1; --bd: rgba(255,255,255,.09);
      --mut: #9ba0a9; --acc: #a8c7fa;
      --card: rgba(255,255,255,.04); --hover: rgba(255,255,255,.07);
      --pbg: #ffffff; --pfg: #0d0d0f;
      --danger: #f87171; --ok: #4ade80;
      --ease: cubic-bezier(.22,.9,.3,1); }
    @media (prefers-color-scheme: light) {
      :host { --bg: #ffffff; --bg2: #f7f7f8; --fg: #0d0d0f; --bd: rgba(0,0,0,.09);
        --mut: #6b7280; --acc: #2563eb; --card: rgba(0,0,0,.03); --hover: rgba(0,0,0,.05);
        --pbg: #0d0d0f; --pfg: #ffffff; }
    }
    * { box-sizing: border-box; }
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-thumb { background: rgba(128,134,148,.35); border-radius: 8px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(128,134,148,.55); }
    ::-webkit-scrollbar-track { background: transparent; }
    @keyframes mh-in { from { opacity: 0; transform: translateY(10px) scale(.975); }
      to { opacity: 1; transform: none; } }
    @keyframes mh-fade { from { opacity: 0; } to { opacity: 1; } }
    @keyframes mh-drawer { from { transform: translateX(40px); opacity: 0; } to { transform: none; opacity: 1; } }
    @keyframes mh-shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
    @keyframes mh-toast { from { opacity: 0; transform: translate(-50%, 12px); }
      to { opacity: 1; transform: translate(-50%, 0); } }
    @media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
    .backdrop { position: fixed; inset: 0; z-index: 2147483000; display: flex; align-items: center;
      justify-content: center; pointer-events: auto; background: rgba(0,0,0,.55);
      backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
      animation: mh-fade .16s var(--ease);
      font: 14px/1.55 var(--font, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif); }
    .panel { width: min(1320px, 94vw); height: min(860px, 90vh); border-radius: 14px; overflow: hidden;
      display: flex; flex-direction: column; background: var(--bg); color: var(--fg);
      border: 1px solid var(--bd); animation: mh-in .2s var(--ease);
      box-shadow: 0 24px 70px rgba(0,0,0,.5), 0 1px 0 rgba(255,255,255,.04) inset; }
    header { display: flex; align-items: center; gap: 10px; padding: 11px 14px;
      border-bottom: 1px solid var(--bd); background: var(--bg2); flex: none; }
    header .mark { width: 24px; height: 24px; border-radius: 7px; background: var(--card);
      border: 1px solid var(--bd); display: flex; align-items: center; justify-content: center;
      color: var(--fg); flex: none; }
    header .logo { font-weight: 600; font-size: 14px; letter-spacing: .1px; white-space: nowrap; }
    header .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--mut); flex: none;
      transition: background .3s; }
    header .dot.ok { background: var(--ok); }
    header .dot.fail { background: var(--danger); }
    nav.tabs { display: flex; gap: 2px; flex: 1; overflow-x: auto; background: var(--card);
      border: 1px solid var(--bd); border-radius: 10px; padding: 3px; scrollbar-width: none; }
    nav.tabs::-webkit-scrollbar { display: none; }
    nav.tabs button { all: unset; cursor: pointer; padding: 5px 13px; border-radius: 8px; color: var(--mut);
      font-size: 13px; white-space: nowrap; transition: color .15s, background .15s; }
    nav.tabs button:hover { color: var(--fg); }
    nav.tabs button.on { background: var(--bg); color: var(--fg); font-weight: 600;
      box-shadow: 0 1px 4px rgba(0,0,0,.3), 0 0 0 1px var(--bd); }
    nav.tabs button .n { font-size: 11px; color: var(--mut); margin-left: 5px; font-weight: 500; }
    .x { all: unset; cursor: pointer; color: var(--mut); font-size: 15px; width: 28px; height: 28px;
      display: flex; align-items: center; justify-content: center; border-radius: 8px; transition: all .15s; }
    .x:hover { background: var(--hover); color: var(--fg); }
    main { flex: 1; overflow: auto; padding: 18px 20px; position: relative; }
    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(176px, 1fr)); gap: 10px; margin-bottom: 18px; }
    .card { background: var(--card); border: 1px solid var(--bd); border-radius: 12px; padding: 13px 15px;
      transition: border-color .15s; }
    .card:hover { border-color: rgba(255,255,255,.18); }
    .card .k { color: var(--mut); font-size: 12px; letter-spacing: .3px; }
    .card .v { font-size: 23px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums;
      letter-spacing: -.3px; }
    h3 { margin: 20px 0 10px; font-size: 12px; color: var(--mut); font-weight: 600;
      letter-spacing: .8px; text-transform: uppercase; }
    .chips { display: flex; flex-wrap: wrap; gap: 7px; }
    .chip { all: unset; cursor: pointer; font-size: 12.5px; padding: 4px 12px; border-radius: 999px;
      background: var(--card); border: 1px solid var(--bd); color: var(--fg);
      transition: border-color .15s, background .15s; }
    .chip:hover { border-color: rgba(255,255,255,.25); background: var(--hover); }
    .chip b { color: var(--mut); margin-left: 5px; font-weight: 500; font-variant-numeric: tabular-nums; }
    .toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
    input, select, textarea { font: inherit; color: var(--fg); background: var(--card);
      border: 1px solid var(--bd); border-radius: 10px; padding: 7px 12px; outline: none;
      transition: border-color .15s, box-shadow .15s; }
    input:focus, select:focus, textarea:focus { border-color: var(--acc);
      box-shadow: 0 0 0 3px rgba(168,199,250,.22); }
    input[type="search"] { min-width: 230px; }
    .btn { all: unset; cursor: pointer; font-size: 13px; padding: 7px 15px; border-radius: 10px;
      background: var(--card); border: 1px solid var(--bd); color: var(--fg);
      transition: border-color .15s, transform .08s, filter .15s; }
    .btn:hover { border-color: rgba(255,255,255,.25); background: var(--hover); }
    .btn:active { transform: scale(.97); }
    .btn.primary { background: var(--pbg); color: var(--pfg); border: 1px solid transparent;
      font-weight: 600; }
    .btn.primary:hover { opacity: .88; background: var(--pbg); }
    .btn.danger { color: var(--danger); border-color: rgba(248,113,113,.35); }
    .btn.danger:hover { border-color: var(--danger); background: rgba(248,113,113,.08); }
    .btn:disabled { opacity: .45; cursor: default; transform: none; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; color: var(--mut); font-weight: 600; font-size: 11.5px; letter-spacing: .5px;
      padding: 8px 12px; border-bottom: 1px solid var(--bd); position: sticky; top: -18px;
      background: var(--bg); z-index: 2; }
    td { padding: 9px 12px; border-bottom: 1px solid var(--bd); vertical-align: top; }
    tbody tr { cursor: pointer; position: relative; transition: background .12s; }
    tbody tr:hover { background: var(--hover); }
    td .sub { color: var(--mut); font-size: 11px; margin-top: 2px; font-family: ui-monospace, Menlo, monospace; }
    .tag { display: inline-block; font-size: 11px; padding: 1px 9px; border-radius: 999px;
      background: var(--hover); color: var(--mut); margin: 1px 4px 1px 0; }
    .pager { display: flex; align-items: center; gap: 12px; margin-top: 14px; color: var(--mut);
      font-size: 12.5px; font-variant-numeric: tabular-nums; }
    .empty { text-align: center; color: var(--mut); padding: 64px 20px; animation: mh-fade .2s var(--ease); }
    .empty .ico { font-size: 36px; margin-bottom: 12px; filter: grayscale(.3); }
    .err { background: rgba(248,113,113,.08); border: 1px solid rgba(248,113,113,.4); color: var(--danger);
      border-radius: 12px; padding: 13px 15px; margin-bottom: 12px; font-size: 13px;
      display: flex; align-items: center; gap: 12px; justify-content: space-between;
      animation: mh-fade .2s var(--ease); }
    .spin { color: var(--mut); padding: 40px; text-align: center; }
    .skl { border-radius: 10px; height: 15px; margin: 12px 0;
      background: linear-gradient(90deg, var(--card) 25%, var(--hover) 50%, var(--card) 75%);
      background-size: 200% 100%; animation: mh-shimmer 1.3s linear infinite; }
    .skl.w40 { width: 40%; } .skl.w70 { width: 70%; } .skl.w90 { width: 90%; }
    pre { background: var(--card); border: 1px solid var(--bd); border-radius: 12px; padding: 14px;
      overflow: auto; font: 12px/1.65 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; }
    .obs { border-bottom: 1px solid var(--bd); padding: 10px 4px; font-size: 13px; }
    .obs:hover { background: var(--hover); border-radius: 8px; }
    .obs .m { color: var(--mut); font-size: 11px; margin-bottom: 3px;
      font-family: ui-monospace, Menlo, monospace; }
    .drawer { position: absolute; top: 0; right: 0; bottom: 0; width: min(780px, 94%);
      background: var(--bg2); border-left: 1px solid var(--bd);
      box-shadow: -24px 0 60px rgba(0,0,0,.4); display: flex; flex-direction: column; z-index: 5;
      animation: mh-drawer .22s var(--ease); }
    .drawer .crumb { font-size: 12px; color: var(--mut); font-family: ui-monospace, Menlo, monospace;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 60%; }
    .drawer .body { flex: 1; overflow: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
    .drawer label { font-size: 11.5px; color: var(--mut); display: block; margin-bottom: 4px;
      letter-spacing: .4px; }
    .drawer textarea { flex: 1; min-height: 300px; resize: none;
      font: 12.5px/1.65 ui-monospace, SFMono-Regular, Menlo, monospace; }
    .drawer footer { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--bd); }
    .toast { position: fixed; bottom: 28px; left: 50%; transform: translateX(-50%);
      background: var(--bg2); color: var(--fg); border: 1px solid var(--bd); border-radius: 10px;
      padding: 10px 20px; font-size: 13px; z-index: 2147483100; pointer-events: none;
      box-shadow: 0 12px 36px rgba(0,0,0,.45); animation: mh-toast .22s var(--ease); }
    .toast.ok { border-color: rgba(74,222,128,.45); }
    .toast.err { border-color: rgba(248,113,113,.5); color: var(--danger); }
    .fab { position: fixed; right: 22px; bottom: 96px; width: 42px; height: 42px; border-radius: 50%;
      background: var(--bg2); border: 1px solid var(--bd); color: var(--fg);
      display: flex; align-items: center; justify-content: center;
      cursor: pointer; z-index: 2147482999; pointer-events: auto; transition: background .15s;
      box-shadow: 0 8px 24px rgba(0,0,0,.4); }
    .fab:hover { background: var(--hover); }
    details { margin-top: 12px; } summary { cursor: pointer; color: var(--mut); font-size: 13px; }
    summary:hover { color: var(--fg); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
    .badge { display: inline-block; font-size: 10.5px; padding: 0 7px; border-radius: 999px;
      border: 1px solid var(--bd); color: var(--mut); margin-right: 4px; }
    .badge.b-mcp { color: var(--acc); border-color: var(--acc); }
    .seg { display: flex; gap: 2px; background: var(--card); border: 1px solid var(--bd);
      border-radius: 10px; padding: 3px; }
    .seg button { all: unset; cursor: pointer; padding: 5px 12px; border-radius: 8px;
      font-size: 12.5px; color: var(--mut); }
    .seg button:hover { color: var(--fg); }
    .seg button.on { background: var(--bg); color: var(--fg); font-weight: 600;
      box-shadow: 0 1px 4px rgba(0,0,0,.3), 0 0 0 1px var(--bd); }
    .pwrap { display: flex; gap: 14px; align-items: flex-start; }
    .vtree { width: 190px; flex: none; overflow: auto; border: 1px solid var(--bd);
      border-radius: 12px; padding: 6px; max-height: calc(90vh - 150px); }
    .vtree .vi { display: flex; justify-content: space-between; gap: 8px; padding: 5px 9px;
      border-radius: 8px; font-size: 12.5px; cursor: pointer; color: var(--mut);
      font-family: ui-monospace, Menlo, monospace; }
    .vtree .vi:hover { background: var(--hover); color: var(--fg); }
    .vtree .vi.on { background: var(--hover); color: var(--fg); font-weight: 600; }
    .vtree .vi .c { font-variant-numeric: tabular-nums; opacity: .65; }
    .pmain { flex: 1; min-width: 0; }
    .live-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--mut); flex: none; }
    .live-dot.on { background: var(--ok); animation: mh-pulse 1.6s ease-in-out infinite; }
    @keyframes mh-pulse { 0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(74,222,128,.5); }
      50% { opacity: .65; box-shadow: 0 0 0 5px rgba(74,222,128,0); } }
    .status-pills { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px; }
    .sp { display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px;
      border-radius: 999px; border: 1px solid var(--bd); background: var(--card);
      font-size: 12.5px; color: var(--fg); font-variant-numeric: tabular-nums; }
    .sp .sd { width: 7px; height: 7px; border-radius: 50%; background: var(--mut); flex: none; }
    .sp.ok .sd { background: var(--ok); }
    .sp.warn .sd { background: #facc15; }
    .sp.bad .sd { background: var(--danger); }
    .sp.muted .sd { background: var(--mut); }
    .sp .sl { color: var(--mut); font-size: 11.5px; letter-spacing: .3px; }
    .pipe { display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
      padding: 14px 14px 14px 4px; margin-bottom: 18px;
      border: 1px solid var(--bd); border-radius: 12px; background: var(--card); }
    .pipe .stage { display: flex; flex-direction: column; gap: 3px; padding: 10px 14px;
      border: 1px solid var(--bd); border-radius: 10px; background: var(--bg);
      min-width: 130px; }
    .pipe .stage .sn { font-size: 12.5px; font-weight: 600; letter-spacing: .3px; }
    .pipe .stage .sm { color: var(--mut); font-size: 11.5px; font-family: ui-monospace, Menlo, monospace;
      font-variant-numeric: tabular-nums; }
    .pipe .sep { color: var(--mut); font-size: 18px; font-weight: 400; flex: none; }
    .pipe-empty { color: var(--mut); font-size: 12.5px; padding: 6px 4px; }
    .recent { border: 1px solid var(--bd); border-radius: 12px; background: var(--card);
      overflow: hidden; }
    .recent .row { display: flex; align-items: center; gap: 12px; padding: 8px 14px;
      border-bottom: 1px solid var(--bd); cursor: pointer; transition: background .12s;
      font-size: 13px; }
    .recent .row:last-child { border-bottom: none; }
    .recent .row:hover { background: var(--hover); }
    .recent .row .rp { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
      white-space: nowrap; font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; }
    .recent .row .rt { color: var(--mut); font-size: 12px; flex: none; }
  `;

  // ============================================================
  // 区域 6/14：shadow host、面板骨架与 toast
  // ============================================================
  const host = document.createElement("div");
  host.id = HOST_ID;
  host.style.cssText = "position:fixed;inset:0;z-index:2147483000;pointer-events:none;";
  // 继承宿主（Codex）字体，与周边 UI 保持一致
  try {
    const f = getComputedStyle(document.body).fontFamily;
    if (f) host.style.setProperty("--font", f);
  } catch {}
  const shadow = host.attachShadow({ mode: "open" });
  const styleEl = document.createElement("style");
  styleEl.textContent = CSS;
  shadow.appendChild(styleEl);

  let toastTimer = 0;
  function toast(msg, ms = 2600, type = "") {
    shadow.querySelector(".toast")?.remove();
    const el = document.createElement("div");
    el.className = "toast" + (type ? " " + type : "");
    el.textContent = msg;
    shadow.appendChild(el);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.remove(), ms);
  }

  const state = {
    open: false,
    tab: "overview",
    health: "unknown",
    overview: null,
    pages: { offset: 0, limit: PAGE_SIZE, type: "", tag: "", q: "", dir: "" },
    obs: { offset: 0, limit: PAGE_SIZE, q: "", project: "", items: [], hasMore: false },
    editor: null, // { path, meta, body, isNew }
  };

  let backdrop = null;
  let contentEl = null;
  let dotEl = null;
  let tabsEl = null;

  const TABS = [
    ["overview", "概览"], ["calls", "调用日志"], ["live", "实时上下文"],
    ["obsidian", "Obsidian"], ["tags", "标签"], ["status", "状态"],
  ];

  function buildPanel() {
    backdrop = document.createElement("div");
    backdrop.className = "backdrop";
    backdrop.innerHTML = `
      <div class="panel" role="dialog" aria-label="Memory Hub 管理面板">
        <header>
          <span class="mark"><svg width="13" height="13" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/>
            <path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3"/></svg></span>
          <span class="logo">Memory Hub</span>
          <span class="dot" title="服务状态"></span>
          <nav class="tabs">${TABS.map(([id, label]) =>
            `<button data-tab="${id}">${label}</button>`).join("")}</nav>
          <button class="x" title="关闭 (Esc)">✕</button>
        </header>
        <main></main>
      </div>`;
    shadow.appendChild(backdrop);
    contentEl = backdrop.querySelector("main");
    dotEl = backdrop.querySelector(".dot");
    tabsEl = backdrop.querySelector(".tabs");
    backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) closePanel(); });
    backdrop.querySelector(".x").addEventListener("click", closePanel);
    tabsEl.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tab]");
      if (b) switchTab(b.dataset.tab);
    });
    updateDot();
  }

  function updateDot() {
    if (!dotEl) return;
    dotEl.className = "dot" + (state.health === "ok" ? " ok" : state.health === "fail" ? " fail" : "");
    dotEl.title = state.health === "ok" ? `服务正常：${API_BASE}`
      : state.health === "fail" ? `服务不可达：${API_BASE}` : "服务状态未知";
  }

  function openPanel() {
    if (state.open) return;
    state.open = true;
    if (!backdrop) buildPanel();
    backdrop.style.display = "flex";
    switchTab(state.tab);
    checkHealth();
  }
  function closePanel() {
    state.open = false;
    if (backdrop) backdrop.style.display = "none";
  }

  // ============================================================
  // 区域 7/14：视图骨架（loading / 错误 / 空态）与路由
  // ============================================================
  // 骨架屏：替代“加载中”文本，观感更接近 Codex 的内容占位
  const skeletonHtml = (rows = 4) =>
    Array.from({ length: rows }, (_, i) =>
      `<div class="skl ${["w90", "w70", "w40", "w70", "w90"][i % 5]}"></div>`).join("");
  function showLoading() { contentEl.innerHTML = skeletonHtml(5); }
  function showError(err, retry) {
    contentEl.innerHTML = `<div class="err"><span>⚠ ${esc(err.message || err)}</span><button class="btn">重试</button></div>`;
    contentEl.querySelector("button").addEventListener("click", retry);
  }
  const emptyHtml = (ico, text, hint = "") =>
    `<div class="empty"><div class="ico">${ico}</div><div>${esc(text)}</div>${hint ? `<div style="margin-top:6px;font-size:12px">${esc(hint)}</div>` : ""}</div>`;

  function switchTab(tab) {
    state.tab = tab;
    tabsEl?.querySelectorAll("button").forEach((b) =>
      b.classList.toggle("on", b.dataset.tab === tab));
    closeDrawer();
    stopLivePoll();  // 离开页签时停掉轮询与图谱渲染循环
    stopGraph();
    ({ overview: viewOverview, calls: viewCalls, live: viewLive, obsidian: viewObsidian,
       tags: viewTags, status: viewStatus }[tab] || viewOverview)();
  }

  // 页签计数徽标（页面总数加载后显示）
  function setTabBadge(tab, n) {
    const b = tabsEl?.querySelector(`button[data-tab="${tab}"]`);
    if (!b) return;
    b.innerHTML = esc(TABS.find(([id]) => id === tab)[1]) +
      (n ? `<span class="n">${n}</span>` : "");
  }

  // Obsidian 页签的子模式：graph=Obsidian 原图 / canvas=内置交互图 / pages=页面管理
  state.obs = { mode: "graph" };
  function gotoPages(setup) {
    if (setup) setup(state.pages);
    state.obs.mode = "pages";
    switchTab("obsidian");
  }

  // ============================================================
  // 区域 8/14：视图「概览」— 统计卡片 + 类型分布 + 阶段耗时
  // ============================================================
  async function viewOverview() {
    showLoading();
    let d;
    try { d = await api("/api/overview"); state.overview = d; }
    catch (e) { return showError(e, viewOverview); }
    const cards = [
      ["知识库页面", d.wiki_pages], ["观察记录", d.observations_lines],
      ["暂存文件", d.observations_files], ["索引大小", fmtBytes(d.index_db_bytes)],
      ["最近采集", fmtAge(d.last_capture_age_seconds)], ["近3日会话", d.sessions_recent],
    ];
    const tRows = Object.entries(d.timings || {}).map(([k, t]) =>
      `<tr><td>${esc(k)}</td><td>${t.count}</td><td>${(t.total_ms / 1000).toFixed(1)}s</td><td>${(t.last_ms / 1000).toFixed(1)}s</td></tr>`).join("");
    contentEl.innerHTML = `
      <div class="cards">${cards.map(([k, v]) =>
        `<div class="card"><div class="k">${k}</div><div class="v">${v ?? "—"}</div></div>`).join("")}</div>
      <h3>类型分布（点击跳转过滤）</h3>
      <div class="chips">${Object.entries(d.by_type || {}).map(([t, n]) =>
        `<button class="chip" data-type="${esc(t)}">${esc(t)}<b>${n}</b></button>`).join("")}</div>
      ${tRows ? `<h3>管道阶段耗时</h3>
      <table><thead><tr><th>阶段</th><th>次数</th><th>累计</th><th>最近</th></tr></thead>
      <tbody>${tRows}</tbody></table>` : ""}`;
    contentEl.querySelectorAll(".chip[data-type]").forEach((c) =>
      c.addEventListener("click", () => {
        gotoPages((p) => { p.type = c.dataset.type; p.tag = ""; p.q = ""; p.offset = 0; });
      }));
  }

  // ============================================================
  // 区域 9/14：视图「记忆页面」— 过滤 / 分页表格 / 新建
  // ============================================================
  let pagesRoot = null;
  async function viewPages(root) {
    pagesRoot = root || contentEl;
    const p = state.pages;
    // 双栏：左 vault 目录树（对齐 Obsidian 文件管理器），右页面列表
    pagesRoot.innerHTML = `
      <div class="pwrap">
        <div class="vtree" data-role="tree"><div class="spin">目录…</div></div>
        <div class="pmain">
          <div class="toolbar">
            <input type="search" placeholder="搜索标题 / 摘要 / 路径 / 标签…" value="${esc(p.q)}">
            <select data-role="type"><option value="">全部类型</option></select>
            <select data-role="tag"><option value="">全部标签</option></select>
            <button class="btn primary" data-role="new">＋ 新建页面</button>
            <button class="btn" data-role="refresh">刷新</button>
          </div>
          <div data-role="list"><div class="spin">加载中…</div></div>
          <div class="pager" data-role="pager"></div>
        </div>
      </div>`;
    const treeEl = pagesRoot.querySelector('[data-role="tree"]');
    api("/api/tree").then((d) => {
      const item = (dir, label, count) =>
        `<div class="vi ${p.dir === dir ? "on" : ""}" data-dir="${esc(dir)}">
           <span>${esc(label)}</span><span class="c">${count}</span></div>`;
      treeEl.innerHTML = item("", "全部", d.total) +
        d.folders.map((f) => item(f.dir, f.dir === "." ? "（根目录）" : f.dir, f.count)).join("");
      treeEl.querySelectorAll(".vi").forEach((el) =>
        el.addEventListener("click", () => {
          p.dir = el.dataset.dir; p.offset = 0;
          treeEl.querySelectorAll(".vi").forEach((x) => x.classList.toggle("on", x === el));
          loadPageList();
        }));
    }).catch(() => { treeEl.innerHTML = ""; });
    const typeSel = pagesRoot.querySelector('[data-role="type"]');
    const tagSel = pagesRoot.querySelector('[data-role="tag"]');
    // 类型/标签下拉用缓存数据填充（接口失败不阻塞列表）
    if (state.overview) {
      typeSel.innerHTML += Object.keys(state.overview.by_type || {})
        .map((t) => `<option ${t === p.type ? "selected" : ""}>${esc(t)}</option>`).join("");
    } else if (p.type) {
      typeSel.innerHTML += `<option selected>${esc(p.type)}</option>`;
    }
    api("/api/tags").then((d) => {
      tagSel.innerHTML += d.tags.map((t) =>
        `<option value="${esc(t.tag)}" ${t.tag === p.tag ? "selected" : ""}>${esc(t.tag)} (${t.count})</option>`).join("");
    }).catch(() => {});
    const reload = () => { p.offset = 0; loadPageList(); };
    const searchInput = pagesRoot.querySelector('input[type="search"]');
    // 闭包引用输入框，不依赖事件 target（shadow DOM 里 target 可能因重定向拿不到）
    searchInput.addEventListener("input",
      debounce(() => { p.q = searchInput.value.trim(); reload(); }, 300));
    typeSel.addEventListener("change", () => { p.type = typeSel.value; reload(); });
    tagSel.addEventListener("change", () => { p.tag = tagSel.value; reload(); });
    pagesRoot.querySelector('[data-role="refresh"]').addEventListener("click", loadPageList);
    pagesRoot.querySelector('[data-role="new"]').addEventListener("click", openNewPageDrawer);
    loadPageList();
  }

  async function loadPageList() {
    const p = state.pages;
    const root = pagesRoot || contentEl;
    const listEl = root.querySelector('[data-role="list"]');
    const pagerEl = root.querySelector('[data-role="pager"]');
    if (!listEl) return;
    listEl.innerHTML = skeletonHtml(6);
    const qs = `?offset=${p.offset}&limit=${p.limit}` +
      (p.dir ? `&dir=${encodeURIComponent(p.dir)}` : "") +
      (p.type ? `&type=${encodeURIComponent(p.type)}` : "") +
      (p.tag ? `&tag=${encodeURIComponent(p.tag)}` : "") +
      (p.q ? `&q=${encodeURIComponent(p.q)}` : "");
    let d;
    try { d = await api("/api/pages" + qs); }
    catch (e) { listEl.innerHTML = `<div class="err"><span>⚠ ${esc(e.message)}</span></div>`; return; }
    setTabBadge("obsidian", d.total);
    if (!d.items.length) {
      listEl.innerHTML = emptyHtml("🗂️", p.q || p.type || p.tag ? "没有匹配的页面" : "知识库还没有页面",
        p.q || p.type || p.tag ? "换个关键词或清除过滤条件" : "先运行 memory-hub.sh run --apply 发布蒸馏页");
      pagerEl.innerHTML = "";
      return;
    }
    listEl.innerHTML = `<table>
      <thead><tr><th>标题</th><th>类型</th><th>标签</th><th>更新</th><th>大小</th></tr></thead>
      <tbody>${d.items.map((it) => `<tr data-path="${esc(it.path)}">
        <td>${esc(it.title)}<div class="sub">${esc(it.path)}</div></td>
        <td>${esc(it.type)}</td>
        <td>${it.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</td>
        <td>${fmtDate(it.updated)}</td><td>${fmtBytes(it.size)}</td></tr>`).join("")}</tbody></table>`;
    listEl.querySelectorAll("tr[data-path]").forEach((tr) =>
      tr.addEventListener("click", () => openEditor(tr.dataset.path)));
    const from = d.total ? d.offset + 1 : 0;
    const to = Math.min(d.offset + d.limit, d.total);
    pagerEl.innerHTML = `
      <button class="btn" data-pg="-1" ${d.offset === 0 ? "disabled" : ""}>← 上一页</button>
      <span>${from}–${to} / ${d.total}</span>
      <button class="btn" data-pg="1" ${to >= d.total ? "disabled" : ""}>下一页 →</button>`;
    pagerEl.querySelectorAll("button[data-pg]").forEach((b) =>
      b.addEventListener("click", () => {
        p.offset = Math.max(0, p.offset + Number(b.dataset.pg) * p.limit);
        loadPageList();
      }));
  }

  // ============================================================
  // 区域 10/14：编辑器抽屉 — 查看 / 编辑 / 保存 / 删除 / 新建
  // ============================================================
  let drawerEl = null;
  function closeDrawer() { drawerEl?.remove(); drawerEl = null; state.editor = null; }

  async function openEditor(path) {
    closeDrawer();
    drawerEl = document.createElement("div");
    drawerEl.className = "drawer";
    drawerEl.innerHTML = `<header><span class="crumb">${esc(path)}</span>
      <span style="flex:1"></span><button class="x">✕</button></header>
      <div class="body"><div class="spin">加载中…</div></div>`;
    contentEl.appendChild(drawerEl);
    drawerEl.querySelector(".x").addEventListener("click", closeDrawer);
    let d;
    try { d = await api("/api/page?path=" + encodeURIComponent(path)); }
    catch (e) {
      drawerEl.querySelector(".body").innerHTML = `<div class="err"><span>⚠ ${esc(e.message)}</span></div>`;
      return;
    }
    const { meta, body } = parseMarkdown(d.content);
    state.editor = { path: d.path, meta, body, isNew: false };
    renderEditor();
  }

  function openNewPageDrawer() {
    closeDrawer();
    const dir = state.pages.type || "concepts";
    const slug = `new-${Date.now().toString(36)}`;
    state.editor = {
      path: `${dir}/${slug}.md`, isNew: true,
      meta: { type: dir.replace(/s$/, ""), title: "", tags: [],
              created: new Date().toISOString(), updated: new Date().toISOString() },
      body: "# 标题\n\n",
    };
    drawerEl = document.createElement("div");
    drawerEl.className = "drawer";
    contentEl.appendChild(drawerEl);
    renderEditor();
  }

  function renderEditor() {
    const ed = state.editor;
    if (!ed || !drawerEl) return;
    // tags 容错：标量字符串（如 tags: foo）也归一成数组
    const tagsArr = Array.isArray(ed.meta.tags) ? ed.meta.tags
      : String(ed.meta.tags || "").split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    const knownTypes = [...new Set(["concept", "entity", "decision", "failure", "comparison",
      "query", "note", "draft", "extract", "summary", "reference", "moc", "atom",
      ...Object.keys(state.overview?.by_type || {}), ed.meta.type || ""].filter(Boolean))];
    drawerEl.innerHTML = `
      <header><span class="crumb">${ed.isNew ? "新建页面" : esc(ed.path)}</span>
        <span style="flex:1"></span><button class="x">✕</button></header>
      <div class="body">
        ${ed.isNew ? `<div><label>保存路径（相对 wiki 根，.md 结尾）</label>
          <input data-f="path" style="width:100%" value="${esc(ed.path)}"></div>` : ""}
        <div><label>标题</label><input data-f="title" style="width:100%" value="${esc(ed.meta.title || "")}"></div>
        <div style="display:flex;gap:10px">
          <div style="flex:1"><label>类型</label>
            <select data-f="type" style="width:100%">${knownTypes.map((t) =>
              `<option ${t === ed.meta.type ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
          <div style="flex:2"><label>标签（逗号分隔）</label>
            <input data-f="tags" style="width:100%" value="${esc(tagsArr.join(", "))}"></div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column"><label>正文（Markdown；frontmatter 其余字段原样保留）</label>
          <textarea data-f="body">${esc(ed.body)}</textarea></div>
      </div>
      <footer>
        <button class="btn primary" data-a="save">${ed.isNew ? "创建" : "保存"}</button>
        ${ed.isNew ? "" : '<button class="btn danger" data-a="del">删除</button>'}
        ${ed.isNew ? "" : '<button class="btn" data-a="obs" title="在 Obsidian 中打开此页">↗ Obsidian</button>'}
        <span style="flex:1"></span>
        <button class="btn" data-a="close">关闭</button>
      </footer>`;
    drawerEl.querySelector(".x").addEventListener("click", closeDrawer);
    drawerEl.querySelector('[data-a="close"]').addEventListener("click", closeDrawer);
    drawerEl.querySelector('[data-a="save"]').addEventListener("click", saveEditor);
    drawerEl.querySelector('[data-a="del"]')?.addEventListener("click", deleteEditor);
    drawerEl.querySelector('[data-a="obs"]')?.addEventListener("click", () => {
      window.open(obsOpenUri(ed.path), "_self");
    });
  }

  async function saveEditor() {
    const ed = state.editor;
    if (!ed) return;
    const g = (f) => drawerEl.querySelector(`[data-f="${f}"]`);
    const path = ed.isNew ? g("path").value.trim() : ed.path;
    if (!/^[\w./\-\u4e00-\u9fff]+\.md$/.test(path) || path.includes("..")) {
      return toast("路径非法：只能含字母数字/._-/中文，且必须以 .md 结尾");
    }
    ed.meta.title = g("title").value.trim();
    ed.meta.type = g("type").value;
    ed.meta.tags = g("tags").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    ed.meta.updated = new Date().toISOString();
    const content = buildMarkdown(ed.meta, g("body").value);
    const btn = drawerEl.querySelector('[data-a="save"]');
    btn.disabled = true; btn.textContent = "保存中…";
    try {
      await api("/api/page", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, content }) });
      toast(ed.isNew ? "已创建 ✓" : "已保存 ✓", 2600, "ok");
      state.overview = null;
      closeDrawer();
      if (state.tab === "obsidian" && state.obs.mode === "pages") loadPageList();
    } catch (e) {
      toast("保存失败：" + e.message, 4000, "err");
      btn.disabled = false; btn.textContent = ed.isNew ? "创建" : "保存";
    }
  }

  async function deleteEditor() {
    const ed = state.editor;
    if (!ed) return;
    if (!window.confirm(`确定删除 ${ed.path}？\n（会移入 ~/.memory-hub/trash，可恢复）`)) return;
    try {
      await api("/api/page?path=" + encodeURIComponent(ed.path), { method: "DELETE" });
      toast("已移入回收站 ✓", 2600, "ok");
      state.overview = null;
      closeDrawer();
      if (state.tab === "obsidian" && state.obs.mode === "pages") loadPageList();
    } catch (e) { toast("删除失败：" + e.message, 4000, "err"); }
  }

  // ============================================================
  // 区域 11/15：视图「调用日志」— agent 检索/读写调用流水（REST + MCP）
  // ============================================================
  const CALL_KIND_LABEL = { search: "检索", ask: "问答", obs_search: "观察检索",
    pages: "页面列表", "page-read": "读页面", "page-write": "写页面",
    "page-delete": "删页面", overview: "概览", tags: "标签", graph: "图谱",
    observations: "观察", status: "状态", metrics: "指标", calls: "调用日志" };

  function viewCalls() {
    state.calls = state.calls || { kind: "", auto: true };
    const c = state.calls;
    contentEl.innerHTML = `
      <div class="toolbar">
        <select data-role="kind">
          <option value="">全部类型</option>
          <option value="mcp">仅 MCP（agent）</option>
          ${["search", "ask", "obs_search", "page-read", "page-write", "page-delete",
             "pages", "observations", "overview", "graph", "tags"].map((k) =>
            `<option value="${k}" ${c.kind === k ? "selected" : ""}>${CALL_KIND_LABEL[k] || k}</option>`).join("")}
        </select>
        <label style="font-size:12.5px;color:var(--mut);display:flex;align-items:center;gap:5px">
          <input type="checkbox" data-role="auto" ${c.auto ? "checked" : ""} style="width:auto"> 10s 自动刷新</label>
        <button class="btn" data-role="refresh">刷新</button>
        <span style="flex:1"></span>
        <span style="font-size:12px;color:var(--mut)">来源：REST(8787) 与 MCP(stdio) 调用</span>
      </div>
      <div data-role="list">${skeletonHtml(6)}</div>
      <div class="pager" data-role="pager"></div>`;
    const reload = () => loadCalls(0);
    contentEl.querySelector('[data-role="kind"]').addEventListener("change", (e) => {
      c.kind = e.target.value; reload();
    });
    contentEl.querySelector('[data-role="auto"]').addEventListener("change", (e) => {
      c.auto = e.target.checked; armCallsPoll();
    });
    contentEl.querySelector('[data-role="refresh"]').addEventListener("click", reload);
    loadCalls(0);
    armCallsPoll();
  }

  let callsTimer = 0;
  let statusTimer = 0;
  function armCallsPoll() {
    clearInterval(callsTimer); callsTimer = 0;
    if (state.tab === "calls" && state.calls?.auto) {
      callsTimer = setInterval(() => loadCalls(0, true), 10000);
    }
  }

  async function loadCalls(offset, quiet = false) {
    const listEl = contentEl.querySelector('[data-role="list"]');
    const pagerEl = contentEl.querySelector('[data-role="pager"]');
    if (!listEl) return;
    if (!quiet) listEl.innerHTML = skeletonHtml(6);
    const kind = state.calls?.kind || "";
    let d;
    try { d = await api(`/api/calls?offset=${offset}&limit=50${kind ? "&kind=" + kind : ""}`); }
    catch (e) { if (!quiet) listEl.innerHTML = `<div class="err"><span>⚠ ${esc(e.message)}</span></div>`; return; }
    if (!d.items.length) {
      listEl.innerHTML = emptyHtml("📡", "还没有调用记录",
        "agent 通过 MCP 工具或 REST 调用 memory-hub 后会出现在这里");
      pagerEl.innerHTML = "";
      return;
    }
    listEl.innerHTML = `<table>
      <thead><tr><th>时间</th><th>来源</th><th>类型</th><th>调用</th><th>状态</th><th>耗时</th></tr></thead>
      <tbody>${d.items.map((it) => `<tr>
        <td style="white-space:nowrap">${fmtDate(it.ts)}</td>
        <td><span class="badge ${it.src === "mcp" ? "b-mcp" : ""}">${esc(it.src || "rest")}</span></td>
        <td>${esc(CALL_KIND_LABEL[it.kind] || it.kind || "")}</td>
        <td class="mono">${esc((it.m || "GET") + " " + (it.q || it.path || "")).slice(0, 120)}${(it.refs || []).length
          ? `<div class="sub">→ ${esc(it.refs.slice(0, 3).join(", "))}${it.refs.length > 3 ? ` +${it.refs.length - 3}` : ""}</div>` : ""}</td>
        <td style="color:${it.status >= 400 ? "var(--danger)" : it.status >= 300 ? "var(--warn, #fbbf24)" : "var(--ok)"}">${it.status || "—"}</td>
        <td>${it.ms ? it.ms + "ms" : "—"}</td></tr>`).join("")}</tbody></table>`;
    const to = Math.min(offset + 50, d.total);
    pagerEl.innerHTML = `
      <button class="btn" data-pg="-1" ${offset === 0 ? "disabled" : ""}>← 上一页</button>
      <span>${d.total ? offset + 1 : 0}–${to} / ${d.total}</span>
      <button class="btn" data-pg="1" ${to >= d.total ? "disabled" : ""}>下一页 →</button>`;
    pagerEl.querySelectorAll("button[data-pg]").forEach((b) =>
      b.addEventListener("click", () => loadCalls(Math.max(0, offset + Number(b.dataset.pg) * 50))));
  }

  // ============================================================
  // 区域 12/15：视图「实时上下文」— observations 流（8s 增量轮询）
  // ============================================================
  const ROLE_COLOR = { user: "#a8c7fa", assistant: "#4ade80", tool: "#fbbf24", system: "#9ba0a9" };

  function viewLive() {
    state.live = state.live || { q: "", project: "", on: true, items: [], lastKey: "" };
    const l = state.live;
    contentEl.innerHTML = `
      <div class="toolbar">
        <span class="live-dot" data-role="pulse"></span>
        <input type="search" placeholder="过滤观察文本…" value="${esc(l.q)}">
        <input data-role="proj" placeholder="项目过滤（精确）" value="${esc(l.project)}" style="min-width:150px">
        <button class="btn" data-role="toggle">${l.on ? "暂停" : "继续"}</button>
        <span style="flex:1"></span>
        <span style="font-size:12px;color:var(--mut)" data-role="count"></span>
      </div>
      <div data-role="list"></div>`;
    const reload = () => { l.items = []; l.lastKey = ""; pollLive(true); };
    const liveSearch = contentEl.querySelector('input[type="search"]');
    const liveProj = contentEl.querySelector('[data-role="proj"]');
    liveSearch.addEventListener("input",
      debounce(() => { l.q = liveSearch.value.trim(); reload(); }, 300));
    liveProj.addEventListener("input",
      debounce(() => { l.project = liveProj.value.trim(); reload(); }, 300));
    contentEl.querySelector('[data-role="toggle"]').addEventListener("click", (e) => {
      l.on = !l.on;
      e.target.textContent = l.on ? "暂停" : "继续";
      if (l.on) pollLive(true);
      updatePulse();
    });
    pollLive(true);
    armLivePoll();
    updatePulse();
  }

  let liveTimer = 0;
  function armLivePoll() {
    clearInterval(liveTimer); liveTimer = 0;
    liveTimer = setInterval(() => {
      if (state.tab === "live" && state.live?.on) pollLive(false);
    }, 8000);
  }
  function armStatusPoll() {
    clearInterval(statusTimer); statusTimer = 0;
    if (state.tab === "status") {
      statusTimer = setInterval(() => { if (state.tab === "status") viewStatus(); }, 15000);
    }
  }
  function stopLivePoll() {
    clearInterval(liveTimer); liveTimer = 0;
    clearInterval(callsTimer); callsTimer = 0;
    clearInterval(statusTimer); statusTimer = 0;
  }
  function updatePulse() {
    const p = contentEl?.querySelector('[data-role="pulse"]');
    if (p) p.classList.toggle("on", !!state.live?.on);
  }

  const liveKey = (it) => (it.created_at || "") + "|" + String(it.text || "").slice(0, 40);

  async function pollLive(full) {
    const l = state.live;
    const listEl = contentEl?.querySelector('[data-role="list"]');
    if (!l || !listEl) return;
    if (full && !l.items.length) listEl.innerHTML = skeletonHtml(5);
    const qs = `?offset=0&limit=50` +
      (l.q ? `&q=${encodeURIComponent(l.q)}` : "") +
      (l.project ? `&project=${encodeURIComponent(l.project)}` : "");
    let d;
    try { d = await api("/api/observations" + qs); }
    catch (e) {
      if (full) listEl.innerHTML = `<div class="err"><span>⚠ ${esc(e.message)}</span></div>`;
      return;
    }
    // 增量合并：以 lastKey 为水位线，只把更新的观察插到头部
    const batch = d.items || [];
    const idx = l.lastKey ? batch.findIndex((i) => liveKey(i) === l.lastKey) : -1;
    const fresh = idx === -1 ? batch : batch.slice(0, idx);
    if (fresh.length || !l.items.length) {
      l.items = fresh.concat(l.items).slice(0, 300);
      if (l.items.length) l.lastKey = liveKey(l.items[0]);
    }
    const countEl = contentEl.querySelector('[data-role="count"]');
    if (countEl) countEl.textContent = `${l.items.length} 条 · 每 8s 更新`;
    if (!l.items.length) {
      listEl.innerHTML = emptyHtml("📭", l.q || l.project ? "没有匹配的观察" : "暂存区还没有观察数据",
        "memory-hub capture/watch 采集会话后，这里会实时滚动");
      return;
    }
    listEl.innerHTML = l.items.map((it) => `
      <div class="obs"><div class="m"><span class="badge" style="color:${ROLE_COLOR[it.role] || "var(--mut)"};border-color:currentColor">${esc(it.role || "?")}</span>
        <span class="badge">${esc(it.type || "")}</span>
        [${esc(it.project || "?")}] · ${esc(it.created_at || "")}</div>
      <div>${esc(it.text)}</div></div>`).join("");
  }


  // ============================================================
  // 区域 13/15：视图「图谱」— Obsidian 风格 [[wikilink]] 力导向关系图
  // ============================================================
  // 深浅两套配色（浅色底用深色系，保证对比度）
  const IS_DARK = window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? true;
  const TYPE_COLORS = IS_DARK
    ? ["#a8c7fa", "#4ade80", "#fbbf24", "#f87171", "#c084fc", "#22d3ee", "#fb923c", "#94a3b8", "#e879f9", "#34d399"]
    : ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#9333ca", "#0891b2", "#ea580c", "#64748b", "#c026d3", "#059669"];
  const GRAPH_LABEL = IS_DARK ? "rgba(236,236,241,.95)" : "rgba(13,13,15,.9)";
  const GRAPH_ORPHAN = IS_DARK ? "rgba(128,134,148,.3)" : "rgba(100,106,118,.3)";
  const GRAPH_EDGE = IS_DARK ? "rgba(128,134,148,.16)" : "rgba(100,106,118,.22)";
  const GRAPH_EDGE_HOT = IS_DARK ? "rgba(168,199,250,.75)" : "rgba(37,99,235,.7)";
  const GRAPH_TAG = IS_DARK ? "#a88afd" : "#7c3aed";  // Obsidian 标签节点的标志性紫色
  const typeColor = (() => {
    const map = new Map();
    return (t) => {
      if (!map.has(t)) map.set(t, TYPE_COLORS[map.size % TYPE_COLORS.length]);
      return map.get(t);
    };
  })();
  let graphCtl = null;
  function stopGraph() { graphCtl?.stop(); graphCtl = null; }

  // Obsidian 页签：图谱（原图/交互）+ 页面管理 三合一
  function viewObsidian() {
    const o = state.obs;
    stopGraph();
    contentEl.innerHTML = `
      <div style="display:flex;flex-direction:column;height:100%">
        <div class="toolbar" style="flex:none;margin-bottom:10px">
          <div class="seg">
            <button data-m="graph" class="${o.mode === "graph" ? "on" : ""}">图谱</button>
            <button data-m="canvas" class="${o.mode === "canvas" ? "on" : ""}">交互图</button>
            <button data-m="pages" class="${o.mode === "pages" ? "on" : ""}">页面管理</button>
          </div>
        </div>
        <div data-role="sub" style="flex:1;min-height:0;position:relative;overflow:auto"></div>
      </div>`;
    contentEl.querySelectorAll(".seg button").forEach((b) =>
      b.addEventListener("click", () => { o.mode = b.dataset.m; viewObsidian(); }));
    const sub = contentEl.querySelector('[data-role="sub"]');
    if (o.mode === "pages") viewPages(sub);
    else viewGraph(sub, o.mode === "canvas" ? "canvas" : "obs");
  }

  async function viewGraph(root, mode) {
    state.graph = state.graph || { atoms: false };
    const g = state.graph;
    root.innerHTML = `
      <div style="display:flex;flex-direction:column;height:100%">
        <div class="toolbar" style="flex:none">
          <label data-role="atomslabel" style="font-size:12.5px;color:var(--mut);display:flex;align-items:center;gap:5px">
            <input type="checkbox" data-role="atoms" ${g.atoms ? "checked" : ""} style="width:auto"> 含有连线的 atom 节点</label>
          <button class="btn" data-role="reload">刷新</button>
          <span style="flex:1"></span>
          <span style="font-size:12px;color:var(--mut)" data-role="gstat"></span>
        </div>
        <div data-role="obsbox" style="display:none;flex:1;min-height:0;border:1px solid var(--bd);border-radius:12px;overflow:auto;background:#0b0b0d"></div>
        <div data-role="gbox" style="display:none;flex:1;min-height:0;border:1px solid var(--bd);border-radius:12px;overflow:hidden">
          <canvas style="width:100%;height:100%;display:block;cursor:grab"></canvas>
        </div>
      </div>`;
    const obsbox = root.querySelector('[data-role="obsbox"]');
    const gbox = root.querySelector('[data-role="gbox"]');
    const statEl = root.querySelector('[data-role="gstat"]');
    const atomsLabel = root.querySelector('[data-role="atomslabel"]');
    root.querySelector('[data-role="reload"]').addEventListener("click", () => viewGraph(root, mode));
    root.querySelector('[data-role="atoms"]').addEventListener("change", (e) => {
      g.atoms = e.target.checked;
      if (mode === "canvas") viewGraph(root, mode);
    });

    if (mode === "obs") {
      // Obsidian 原图：经官方 CLI 激活图谱视图并截屏（数据、配色、布局 1:1 来自 Obsidian）
      obsbox.style.display = "block";
      atomsLabel.style.visibility = "hidden";
      statEl.textContent = "";
      obsbox.innerHTML = `<div class="spin">正在从 Obsidian 截屏…（会短暂激活 Obsidian 窗口）</div>`;
      const img = new Image();
      img.style.cssText = "width:100%;display:block";
      img.onload = () => { obsbox.innerHTML = ""; obsbox.appendChild(img); };
      img.onerror = () => {
        obsbox.innerHTML = emptyHtml("🖥️", "拿不到 Obsidian 截图",
          "确认 Obsidian 正在运行且已打开 llm-wiki vault；或切到「交互图」");
      };
      img.src = `${API_BASE}/api/obsidian/graph-shot?t=${Date.now()}`;
      return;
    }
    // 交互图：可拖拽/缩放/点击打开页面
    gbox.style.display = "block";
    atomsLabel.style.visibility = "";
    const canvas = gbox.querySelector("canvas");
    statEl.textContent = "加载中…";
    let data;
    try { data = await api("/api/graph?include_atoms=" + (g.atoms ? 1 : 0), {}, 60000); }
    catch (e) { statEl.textContent = ""; gbox.innerHTML = `<div class="err"><span>⚠ ${esc(e.message)}</span></div>`; return; }
    if (!data.nodes.length) {
      statEl.textContent = "";
      gbox.innerHTML = emptyHtml("🕸️", "还没有可连线的页面", "页面正文里的 [[wikilink]] 会形成连线");
      return;
    }
    statEl.textContent = `${data.nodes.length} 节点 · ${data.edges.length} 连线（拖动节点 / 滚轮缩放 / 点击节点打开）`;
    graphCtl?.stop();
    graphCtl = runGraph(canvas, data);
  }

  // 迷你力导向布局：斥力 + 弹簧 + 弱向心力，拖拽/缩放/悬停高亮/点击打开
  function runGraph(canvas, data) {
    const ctx = canvas.getContext("2d");
    let W = 0, H = 0, raf = 0, dead = false;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const N = data.nodes.length;
    const R0 = Math.min(600, 40 * Math.sqrt(N));
    const nodes = data.nodes.map((n, i) => ({
      ...n, x: R0 * Math.cos(i * 2.39996), y: R0 * Math.sin(i * 2.39996), vx: 0, vy: 0,
    }));
    const idx = new Map(nodes.map((n, i) => [n.id, i]));
    const edges = data.edges.map((e) => ({ s: idx.get(e.source), t: idx.get(e.target) }))
      .filter((e) => e.s != null && e.t != null);
    const neighbors = new Map();
    for (const e of edges) {
      (neighbors.get(e.s) || neighbors.set(e.s, []).get(e.s)).push(e.t);
      (neighbors.get(e.t) || neighbors.set(e.t, []).get(e.t)).push(e.s);
    }
    let cam = { x: 0, y: 0, k: 1 }, dragN = null, panning = null, hoverI = -1;
    const sample = N > 700 ? 50 : 0;  // 大图册抽样斥力，保帧率

    function resize() {
      const r = canvas.getBoundingClientRect();
      W = r.width; H = r.height;
      canvas.width = W * dpr; canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    const onWinResize = () => resize();
    window.addEventListener("resize", onWinResize);

    function tick() {
      for (let i = 0; i < N; i++) {
        const a = nodes[i];
        const cnt = sample || N;
        for (let j = 0; j < cnt; j++) {
          const bi = sample ? (i * 31 + j * 17) % N : j;
          if (bi === i) continue;
          const b = nodes[bi];
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy || 0.01;
          if (d2 > 40000) continue;
          const f = 900 / d2;
          a.vx += dx * f; a.vy += dy * f;
        }
        a.vx -= a.x * 0.0012; a.vy -= a.y * 0.0012;  // 向心
      }
      for (const e of edges) {
        const a = nodes[e.s], b = nodes[e.t];
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.hypot(dx, dy) || 0.01;
        const f = (d - 70) * 0.008;
        a.vx += dx / d * f; a.vy += dy / d * f;
        b.vx -= dx / d * f; b.vy -= dy / d * f;
      }
      for (const n of nodes) {
        if (n === dragN) continue;
        n.vx *= 0.82; n.vy *= 0.82;
        n.x += Math.max(-8, Math.min(8, n.vx));
        n.y += Math.max(-8, Math.min(8, n.vy));
      }
    }

    const toWorld = (mx, my) => ({ x: (mx - W / 2) / cam.k + cam.x, y: (my - H / 2) / cam.k + cam.y });

    function draw() {
      ctx.clearRect(0, 0, W, H);
      ctx.save();
      ctx.translate(W / 2, H / 2); ctx.scale(cam.k, cam.k); ctx.translate(-cam.x, -cam.y);
      const hl = hoverI >= 0 ? new Set([hoverI, ...(neighbors.get(hoverI) || [])]) : null;
      ctx.lineWidth = 0.6 / cam.k;
      for (const e of edges) {
        const hot = hl && hl.has(e.s) && hl.has(e.t);
        ctx.strokeStyle = hot ? GRAPH_EDGE_HOT : GRAPH_EDGE;
        if (hl && !hot) continue;
        ctx.beginPath();
        ctx.moveTo(nodes[e.s].x, nodes[e.s].y);
        ctx.lineTo(nodes[e.t].x, nodes[e.t].y);
        ctx.stroke();
      }
      for (let i = 0; i < N; i++) {
        const n = nodes[i];
        const deg = neighbors.get(i)?.length || 0;
        if (!deg && !(n.hot > 0)) {  // 孤立且无调用的节点：小灰点，不抢视线
          ctx.beginPath();
          ctx.arc(n.x, n.y, 2 / cam.k ** 0.35, 0, 6.2832);
          ctx.fillStyle = GRAPH_ORPHAN;
          ctx.fill();
          continue;
        }
        // 半径 = 基础 + 连线度 + agent 调用热度（hot 来自 access.jsonl 的 refs 聚合）
        // 标签节点（Obsidian 图谱同款）：紫色、小半径，不随连线度膨胀
        const isTag = n.type === "tag";
        const r = (isTag ? 2.6 : 3 + Math.min(5, deg * 0.6)
          + Math.min(6, (n.hot || 0) * 1.2)) / cam.k ** 0.35;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, 6.2832);
        ctx.fillStyle = hl && !hl.has(i) ? "rgba(128,134,148,.25)"
          : isTag ? GRAPH_TAG : typeColor(n.type);
        ctx.fill();
        if (n.hot) {  // 被 agent 调用过的页面加高亮描边
          ctx.lineWidth = 1.4 / cam.k ** 0.35;
          ctx.strokeStyle = "rgba(251,191,36,.85)";
          ctx.stroke();
        }
      }
      if (hoverI >= 0) {
        const n = nodes[hoverI];
        ctx.font = `${12 / cam.k}px -apple-system, sans-serif`;
        ctx.fillStyle = GRAPH_LABEL;
        const label = n.title.slice(0, 48) + (n.hot ? `  ·  agent 调用 ${n.hot} 次` : "");
        ctx.fillText(label, n.x + 8 / cam.k, n.y + 4 / cam.k);
      }
      ctx.restore();
    }

    function loop() {
      if (dead) return;
      tick(); draw();
      raf = requestAnimationFrame(loop);
    }
    loop();

    const pick = (mx, my) => {
      const w = toWorld(mx, my);
      let best = -1, bd = 12 / cam.k;
      for (let i = 0; i < N; i++) {
        const d = Math.hypot(nodes[i].x - w.x, nodes[i].y - w.y);
        if (d < bd) { bd = d; best = i; }
      }
      return best;
    };
    canvas.addEventListener("mousedown", (e) => {
      const r = canvas.getBoundingClientRect();
      const i = pick(e.clientX - r.left, e.clientY - r.top);
      if (i >= 0) { dragN = nodes[i]; }
      else panning = { sx: e.clientX, sy: e.clientY, cx: cam.x, cy: cam.y };
    });
    canvas.addEventListener("mousemove", (e) => {
      const r = canvas.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      if (dragN) { const w = toWorld(mx, my); dragN.x = w.x; dragN.y = w.y; dragN.vx = dragN.vy = 0; }
      else if (panning) { cam.x = panning.cx - (e.clientX - panning.sx) / cam.k; cam.y = panning.cy - (e.clientY - panning.sy) / cam.k; }
      else hoverI = pick(mx, my);
      canvas.style.cursor = dragN || hoverI >= 0 ? "pointer" : panning ? "grabbing" : "grab";
    });
    const up = (e) => {
      if (dragN && hoverI < 0) { /* 拖拽结束 */ }
      if (dragN) { dragN = null; return; }
      if (panning) {
        const moved = Math.hypot(e.clientX - panning.sx, e.clientY - panning.sy);
        panning = null;
        if (moved < 4 && hoverI >= 0) {
          // 点击节点 → 在「页面管理」抽屉中打开
          const path = nodes[hoverI].id;
          gotoPages();
          openEditor(path);
        }
      }
    };
    canvas.addEventListener("mouseup", up);
    canvas.addEventListener("mouseleave", () => { hoverI = -1; panning = null; dragN = null; });
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      cam.k = Math.max(0.15, Math.min(6, cam.k * (e.deltaY < 0 ? 1.12 : 0.89)));
    }, { passive: false });

    return {
      stop() { dead = true; cancelAnimationFrame(raf); window.removeEventListener("resize", onWinResize); },
    };
  }

  // ============================================================
  // 区域 14/15：视图「标签」与「状态」
  // ============================================================
  async function viewTags() {
    showLoading();
    let d;
    try { d = await api("/api/tags"); }
    catch (e) { return showError(e, viewTags); }
    if (!d.tags.length) {
      contentEl.innerHTML = emptyHtml("🏷️", "还没有标签", "页面 frontmatter 的 tags 字段会自动汇总到这里");
      return;
    }
    contentEl.innerHTML = `<h3>共 ${d.total} 个标签（点击跳转到页面过滤）</h3>
      <div class="chips">${d.tags.map((t) =>
        `<button class="chip" data-tag="${esc(t.tag)}">${esc(t.tag)}<b>${t.count}</b></button>`).join("")}</div>`;
    contentEl.querySelectorAll(".chip[data-tag]").forEach((c) =>
      c.addEventListener("click", () => {
        gotoPages((p) => { p.tag = c.dataset.tag; p.type = ""; p.q = ""; p.offset = 0; });
      }));
  }

  const _PIPE_ORDER = ["capture", "distill", "publish", "embed"];
  function _fmtMs(ms) {
    if (ms == null || Number.isNaN(ms)) return "—";
    return (Number(ms) / 1000).toFixed(1) + "s";
  }
  function _freshnessPill(age) {
    if (age == null || age < 0) return `<span class="sp muted"><span class="sd"></span><span class="sl">采集</span><span>未知</span></span>`;
    if (age < 120) return `<span class="sp ok"><span class="sd"></span><span class="sl">采集</span><span>刚刚</span></span>`;
    if (age < 3600) return `<span class="sp warn"><span class="sd"></span><span class="sl">采集</span><span>${Math.floor(age / 60)} 分钟前</span></span>`;
    if (age < 86400) return `<span class="sp bad"><span class="sd"></span><span class="sl">采集</span><span>${Math.floor(age / 3600)} 小时前</span></span>`;
    return `<span class="sp bad"><span class="sd"></span><span class="sl">采集</span><span>${Math.floor(age / 86400)} 天前</span></span>`;
  }
  async function viewStatus() {
    showLoading();
    let v, statusText, metricsText = "";
    try { v = await api("/api/vitals"); }
    catch (e) { v = null; }
    try { statusText = await api("/status"); }
    catch (e) { return showError(e, viewStatus); }
    try { metricsText = await api("/metrics"); } catch { metricsText = "（metrics 不可用）"; }

    const pills = [];
    pills.push(`<span class="sp ok"><span class="sd"></span><span class="sl">REST</span><span>可用</span></span>`);
    pills.push(v && v.llm_proxy !== false
      ? `<span class="sp ok"><span class="sd"></span><span class="sl">LLM 代理</span><span>可用</span></span>`
      : `<span class="sp bad"><span class="sd"></span><span class="sl">LLM 代理</span><span>不可用</span></span>`);
    const cm = Number(v && v.claude_mem_rows) || 0;
    pills.push(cm > 0
      ? `<span class="sp ok"><span class="sd"></span><span class="sl">claude-mem</span><span>${cm} 行</span></span>`
      : `<span class="sp muted"><span class="sd"></span><span class="sl">claude-mem</span><span>0 行</span></span>`);
    pills.push(_freshnessPill(v && v.last_capture_age_seconds != null ? v.last_capture_age_seconds : null));

    const t = v && v.timings ? v.timings : {};
    const stages = _PIPE_ORDER.filter((k) => Object.prototype.hasOwnProperty.call(t, k));
    let pipeHtml;
    if (stages.length === 0) {
      pipeHtml = `<div class="pipe"><div class="pipe-empty">暂无管道阶段耗时（.memory-hub/timings.tsv 为空）</div></div>`;
    } else {
      const parts = stages.map((k) => {
        const st = t[k] || {};
        return `<div class="stage"><span class="sn">${esc(k)}</span><span class="sm">${Number(st.count || 0)} 次 · 最近 ${_fmtMs(st.last_ms)}</span></div>`;
      });
      pipeHtml = `<div class="pipe">${parts.join('<span class="sep">→</span>')}</div>`;
    }

    const cards = [
      ["近 3 日会话", v ? Number(v.sessions_recent) : "—"],
      ["观察文件", v ? Number(v.obs_files) : "—"],
      ["实时观察", v ? Number(v.realtime_lines) : "—"],
      ["Wiki 页面", v ? Number(v.wiki_pages) : "—"],
      ["索引大小", v ? fmtBytes(Number(v.index_db_bytes)) : "—"],
      ["claude-mem 行数", cm],
    ];

    const pages = (v && Array.isArray(v.recent_pages) ? v.recent_pages : []).slice(0, 5);
    const rows = pages.map((p) => {
      const age = p.mtime != null ? Math.max(0, Math.floor(Date.now() / 1000) - p.mtime) : null;
      return `<div class="row rp-item" data-path="${esc(p.path)}"><span class="rp">${esc(p.path)}</span><span class="rt">${fmtAge(age)}</span></div>`;
    }).join("") || `<div class="row" style="color:var(--mut);cursor:default"><span class="rp">暂无最近页面</span></div>`;

    contentEl.innerHTML = `
      <div class="toolbar"><button class="btn" data-role="refresh">刷新</button>
        <span style="color:var(--mut);font-size:12px">服务：${esc(API_BASE)}（localStorage 键 memoryHubAdmin.apiBase 可改）</span></div>
      <h3>健康状态</h3>
      <div class="status-pills">${pills.join("")}</div>
      <h3>管道流程</h3>
      ${pipeHtml}
      <h3>关键数字</h3>
      <div class="cards">${cards.map(([k, vv]) =>
        `<div class="card"><div class="k">${esc(k)}</div><div class="v">${vv ?? "—"}</div></div>`).join("")}</div>
      <h3>最近更新</h3>
      <div class="recent">${rows}</div>
      <h3 style="margin-top:24px">调试信息</h3>
      <details><summary>REST /status 原始文本</summary><pre>${esc(statusText)}</pre></details>
      <details><summary>Prometheus 指标</summary><pre>${esc(metricsText)}</pre></details>`;

    contentEl.querySelector('[data-role="refresh"]').addEventListener("click", viewStatus);
    contentEl.querySelectorAll(".recent .rp-item").forEach((el) =>
      el.addEventListener("click", () => openEditor(el.dataset.path)));
    armStatusPoll();
  }

  // ============================================================
  // 区域 14/14：入口注入（Codex++ 菜单 → 侧栏 → 浮动兜底）、生命周期
  // ============================================================
  const ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/>
    <path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3"/></svg>`;

  function buildEntryButton() {
    const btn = document.createElement("button");
    btn.setAttribute(ENTRY_ATTR, "1");
    btn.title = "Memory Hub 管理面板";
    btn.style.cssText = `all:unset;cursor:pointer;display:flex;align-items:center;gap:8px;
      padding:7px 12px;margin:2px 6px;border-radius:8px;font-size:13px;color:inherit;`;
    btn.innerHTML = `${ICON}<span>Memory Hub</span>`;
    btn.onmouseenter = () => { btn.style.background = "rgba(128,128,128,.18)"; };
    btn.onmouseleave = () => { btn.style.background = "transparent"; };
    btn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); openPanel(); });
    return btn;
  }

  function findSidebar() {
    // 打分制侧栏探测：参考 codex++ 市场脚本 bennett-ui-improvements 的候选 + 几何过滤
    const cands = document.querySelectorAll(
      'aside, nav, [role="navigation"], [data-testid*="sidebar" i], [class*="sidebar" i]');
    let best = null, bestScore = 0;
    for (const el of cands) {
      if (!el.isConnected) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 120 || r.width > 480 || r.height < 200 || r.left > 100) continue;
      let score = 1;
      if (el.tagName === "ASIDE") score += 2;
      if (/sidebar/i.test(String(el.className) + " " + (el.getAttribute("data-testid") || ""))) score += 2;
      if (el.querySelector('[data-app-action-sidebar-scroll], [data-app-action-sidebar-thread-id]')) score += 3;
      if (score > bestScore) { best = el; bestScore = score; }
    }
    return best;
  }

  function injectEntry() {
    if (document.querySelector(`[${ENTRY_ATTR}]`)) return true;
    // 1) codex++ 自己的菜单（最自然的集成点）
    const menu = document.querySelector('#codex-plus-menu, [data-codex-plus-menu="true"]');
    if (menu) { menu.appendChild(buildEntryButton()); return true; }
    // 2) Codex 左侧边栏：优先挂到其滚动容器尾部
    const sb = findSidebar();
    if (sb) {
      const hostEl = sb.querySelector("[data-app-action-sidebar-scroll]") || sb;
      hostEl.appendChild(buildEntryButton());
      return true;
    }
    return false;
  }

  let fabEl = null;
  function showFab() {
    if (fabEl || document.querySelector(`[${ENTRY_ATTR}]`)) return;
    fabEl = document.createElement("div");
    fabEl.className = "fab";
    fabEl.setAttribute(ENTRY_ATTR, "fab");
    fabEl.title = "Memory Hub 管理面板";
    fabEl.innerHTML = ICON;
    fabEl.addEventListener("click", openPanel);
    shadow.appendChild(fabEl);
  }

  async function checkHealth() {
    try { await api("/health", {}, 5000); state.health = "ok"; }
    catch { state.health = "fail"; }
    updateDot();
  }

  // SPA 路由/DOM 重建后重新注入入口；持续探测直到成功
  const observer = new MutationObserver(debounce(() => {
    if (!document.querySelector(`[${ENTRY_ATTR}]`)) injectEntry();
  }, 400));
  observer.observe(document.documentElement, { childList: true, subtree: true });

  const fallbackTimer = setTimeout(() => { if (!injectEntry()) showFab(); }, SIDEBAR_WAIT_MS);
  const healthTimer = setInterval(checkHealth, 30000);

  const onKey = (e) => {
    if (e.key === "Escape" && state.open) {
      // 分层关闭：先关抽屉，再关面板
      if (state.editor) closeDrawer(); else closePanel();
      return;
    }
    // “/” 快速聚焦当前视图的搜索框（贴合 Codex 键盘流）
    if (e.key === "/" && state.open && !state.editor) {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      const input = contentEl?.querySelector('input[type="search"]');
      if (input) { e.preventDefault(); input.focus(); }
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === "M" || e.key === "m")) {
      e.preventDefault();
      state.open ? closePanel() : openPanel();
    }
  };
  document.addEventListener("keydown", onKey, true);

  // 挂载与全局句柄（harness 契约：open/close/destroy/version）
  document.body.appendChild(host);
  injectEntry();
  checkHealth();

  window[API_KEY] = {
    version: VERSION,
    open: openPanel,
    close: closePanel,
    destroy() {
      observer.disconnect();
      clearTimeout(fallbackTimer);
      clearInterval(healthTimer);
      clearTimeout(toastTimer);
      document.removeEventListener("keydown", onKey, true);
      document.querySelectorAll(`[${ENTRY_ATTR}]`).forEach((n) => n.remove());
      host.remove();
      delete window[API_KEY];
    },
  };
})();
