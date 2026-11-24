// Shared helpers for the three pages.
const COLORS = { arr: "#1D5FA8", dep: "#B9650C", good: "#1B7F4C", bad: "#C23B32", warn: "#E39A1C", flink: "#0F7C78",
                 muted: "#5E6B78", grid: "#E4E8ED", ink: "#17202B", neutral: "#8A96A3", sched: "#9AA6B2" };

if (window.Chart) {
  Chart.defaults.color = COLORS.muted;
  Chart.defaults.borderColor = COLORS.grid;
  Chart.defaults.font.family = '"Barlow", "Segoe UI", system-ui, sans-serif';
  Chart.defaults.font.size = 13;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.boxHeight = 12;
  Chart.defaults.animation = false;
}

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 0) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const hhmm = (iso) => (iso ? iso.slice(11, 16) : "");

// Flight status as coloured text with a small square, like an airport board.
function statusText(status) {
  const cls = { Cancelled: "bad", Diverted: "bad", Delayed: "warn", Boarding: "info", "Gate Closed": "info",
                Approaching: "info", Taxiing: "info", Landed: "good", Arrived: "good", Departed: "good" }[status] || "";
  return `<span class="status ${cls}">${esc(status)}</span>`;
}

// Fetch a JSON endpoint now and every `seconds`; call render(data) each time.
function poll(url, seconds, render) {
  const el = $("updated");
  async function tick() {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      render(data);
      el.classList.remove("stale");
      el.textContent = `Updated ${new Date(data.generated_at).toLocaleTimeString("en-IN", { hour12: false })}, refreshes every ${seconds} s`;
    } catch (e) {
      el.classList.add("stale");
      el.textContent = "No data: check that web_dashboard.py can reach PostgreSQL";
    }
  }
  tick();
  setInterval(tick, seconds * 1000);
}

// Create a chart once, then only replace its data on later refreshes.
const charts = {};
function drawChart(id, config) {
  if (charts[id]) {
    charts[id].data = config.data;
    charts[id].update();
  } else {
    charts[id] = new Chart($(id), config);
  }
}
