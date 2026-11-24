const COLORS = {
  arr: "#1d4ed8",
  dep: "#b45309",
  good: "#047857",
  bad: "#b91c1c",
  warn: "#b45309",
  flink: "#0f766e",
  muted: "#5b6875",
  grid: "rgba(23, 33, 43, 0.08)",
  ink: "#17212b",
  neutral: "#8492a0",
  sched: "#8492a0"
};

if (window.Chart) {
  Chart.defaults.color = COLORS.muted;
  Chart.defaults.borderColor = COLORS.grid;
  Chart.defaults.font.family = '"Barlow", "Segoe UI", system-ui, sans-serif';
  Chart.defaults.font.size = 12.5;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.boxHeight = 12;
  Chart.defaults.plugins.legend.labels.color = "#334155";
  Chart.defaults.animation = false;
}

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 0) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const hhmm = (iso) => (iso ? iso.slice(11, 16) : "");
const emptyRow = (cols, text) => `<tr><td colspan="${cols}" class="empty">${esc(text)}</td></tr>`;
const chip = (text, kind) => `<span class="chip ${kind}">${esc(text)}</span>`;
const icon = (name, cls = "") => `<svg class="icon ${cls}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;

function statusText(status) {
  const cls = { Cancelled: "bad", Diverted: "bad", Delayed: "warn", Boarding: "info", "Gate Closed": "info",
                Approaching: "info", Taxiing: "info", Landed: "good", Arrived: "good", Departed: "good" }[status] || "";
  return `<span class="status ${cls}">${esc(status)}</span>`;
}

function poll(url, seconds, render) {
  const el = $("updated"), banner = $("conn-banner"), badge = document.querySelector(".live-badge");
  let busy = false;
  async function tick() {
    if (busy || document.hidden) return;
    busy = true;
    const controller = new AbortController();
    const limit = setTimeout(() => controller.abort(), Math.max(8000, seconds * 1000));
    try {
      const res = await fetch(url, { cache: "no-store", signal: controller.signal });
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      render(data);
      el.classList.remove("stale");
      el.textContent = `Updated ${new Date(data.generated_at).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" })}, refreshes every ${seconds} s`;
      if (banner) banner.hidden = true;
      if (badge) { badge.textContent = "Stream online"; badge.classList.remove("offline"); }
    } catch (e) {
      el.classList.add("stale");
      el.textContent = "Live data unavailable";
      if (banner) banner.hidden = false;
      if (badge) { badge.textContent = "Stream offline"; badge.classList.add("offline"); }
    } finally {
      clearTimeout(limit);
      busy = false;
    }
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
  setInterval(tick, seconds * 1000);
  tick();
}

function setupTabs(selector) {
  const bar = document.querySelector(selector);
  if (!bar) return;
  const tabs = [...bar.querySelectorAll('[role="tab"]')];
  function select(tab, focus) {
    tabs.forEach((t) => {
      const active = t === tab;
      t.setAttribute("aria-selected", String(active));
      t.tabIndex = active ? 0 : -1;
      const panel = $(t.dataset.tab);
      if (panel) panel.hidden = !active;
    });
    if (tab && tab.dataset.tab) history.replaceState(null, "", "#" + tab.dataset.tab);
    if (focus) tab.focus();
    setTimeout(() => window.dispatchEvent(new Event("resize")), 30);
  }
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      const target = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : step ? (i + step + tabs.length) % tabs.length : -1;
      if (target >= 0) { e.preventDefault(); select(tabs[target], true); }
    });
  });
  const initial = tabs.find((t) => "#" + t.dataset.tab === location.hash) || tabs[0];
  if (initial) select(initial);
}

const charts = {};
function drawChart(id, config) {
  if (charts[id]) {
    charts[id].data = config.data;
    charts[id].options = config.options;
    charts[id].update();
  } else {
    charts[id] = new Chart($(id), config);
  }
}
