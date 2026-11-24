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
const ICONS = {
  sun: '<circle cx="12" cy="12" r="4.5" fill="currentColor"/><path stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M12 1.5v3m0 15v3M1.5 12h3m15 0h3M4.6 4.6l2.1 2.1m10.6 10.6 2.1 2.1m0-14.8-2.1 2.1M6.7 17.3l-2.1 2.1"/>',
  cloud: '<path fill="currentColor" d="M7 18.5a4 4 0 0 1-.4-8A5.5 5.5 0 0 1 17 9.2a3.7 3.7 0 0 1-.2 7.3H7z"/>',
  rain: '<path fill="currentColor" d="M7 15a4 4 0 0 1-.4-8A5.5 5.5 0 0 1 17 5.7a3.7 3.7 0 0 1-.2 7.3H7z"/><path stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="m8 17-1 3m5-3-1 3m5-3-1 3"/>',
  fog: '<path fill="currentColor" d="M7 13a4 4 0 0 1-.4-8A5.5 5.5 0 0 1 17 3.7a3.7 3.7 0 0 1-.2 7.3H7z"/><path stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M4 16h16M6 19h12M9 22h6"/>'
};
const icon = (name) => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ICONS.cloud}</svg>`;

function statusText(status) {
  const cls = { Cancelled: "bad", Diverted: "bad", Delayed: "warn", Boarding: "info", "Gate Closed": "info",
                Approaching: "info", Taxiing: "info", Landed: "good", Arrived: "good", Departed: "good" }[status] || "";
  return `<span class="status ${cls}">${esc(status)}</span>`;
}

function poll(url, seconds, render) {
  const el = $("updated"), banner = $("conn-banner"), badge = document.querySelector(".live-badge");
  let busy = false, lastOk = "";
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
      lastOk = new Date(data.generated_at).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" });
      el.classList.remove("stale");
      el.textContent = `Updated ${lastOk}, refreshes every ${seconds} s`;
      if (banner) banner.hidden = true;
      if (badge) { badge.textContent = "Stream online"; badge.classList.remove("offline"); }
    } catch (e) {
      console.error("[dashboard] data refresh failed", e);
      el.classList.add("stale");
      el.textContent = "Live data unavailable" + (lastOk ? " · last update " + lastOk : "");
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
