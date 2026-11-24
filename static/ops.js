// Flight operations: refreshes every 30 seconds.
const BUSY = { Low: COLORS.good, Moderate: COLORS.warn, High: "#D9622B", Saturated: COLORS.bad };
const lineOpts = (ytitle) => ({
  maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
  plugins: { legend: { position: "top", align: "start" } },
  scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } },
            y: { beginAtZero: true, title: { display: !!ytitle, text: ytitle } } },
});

poll("/api/ops", 30, (d) => {
  const n = d.now, a = d.airspace || {};
  $("k-mov").textContent = fmt(n.movements_last_60);
  $("k-util").textContent = n.utilisation_pct == null ? "–" : fmt(n.utilisation_pct, 1) + " %";
  $("k-cap").textContent = n.airport_capacity_per_hour ? "of " + n.airport_capacity_per_hour + " per hour" : "";
  $("k-busy").textContent = n.busy_level || "–";
  $("k-busy").style.color = BUSY[n.busy_level] || COLORS.ink;
  $("k-plan").textContent = fmt(n.planned_next_60);
  $("k-dd").textContent = n.avg_dep_delay_last_60 == null ? "–" : fmt(n.avg_dep_delay_last_60, 1) + " min";
  $("k-otp").textContent = n.otp_24h_pct == null ? "–" : fmt(n.otp_24h_pct, 1) + " %";
  $("k-air").textContent = fmt(a.airborne);
  $("k-air2").textContent = `${fmt(a.arriving)} arriving, ${fmt(a.departing)} departing`;

  $("runways").innerHTML = d.runways.map((r) => {
    const pct = Math.min(100, r.utilisation_pct || 0), col = BUSY[r.busy_level] || COLORS.neutral;
    const mark = r.used_for === "ARR" ? "arr-mark" : "dep-mark";
    return `<section class="panel runway ${mark}"><div class="panel-head"><h2>Runway ${esc(r.runway)}</h2>
      <span class="hint">${r.used_for === "ARR" ? "Arrivals" : "Departures"}, landing and taking off towards ${esc(r.active_direction)}</span></div>
      <div class="meter" role="img" aria-label="${fmt(r.utilisation_pct, 1)} percent of capacity"><span style="width:${pct}%;background:${col}"></span><i style="left:50%"></i><i style="left:75%"></i><i style="left:90%"></i></div>
      <div class="runway-art"><svg viewBox="0 0 64 24" aria-hidden="true"><path fill="currentColor" d="M20 0h24l8 24H12z"/><path fill="#fff" d="M31 2h2v4h-2zm0 6h2v4h-2zm0 6h2v4h-2zm0 6h2v2h-2z"/></svg><span class="rwy-dir">${esc(r.active_direction)}</span></div>
      <div class="line"><span><strong>${fmt(r.movements_last_60)}</strong> of ${fmt(r.capacity_per_hour)} movements in the last 60 min, ${fmt(r.planned_next_60)} planned in the next 60 min</span>
      <span class="level" style="color:${col}">${fmt(r.utilisation_pct, 1)} %, ${esc(r.busy_level)}</span></div></section>`;
  }).join("");

  $("w-cat").textContent = n.flight_category || "–";
  $("w-vis").textContent = n.vis_km == null ? "–" : fmt(n.vis_km, 1) + " km";
  $("w-ceil").textContent = n.ceiling_ft ? fmt(n.ceiling_ft) + " ft" : "No ceiling";
  $("w-wind").textContent = n.wind_kt == null ? "–" : `${n.wind_dir == null ? "Variable" : String(n.wind_dir).padStart(3, "0") + "°"} / ${n.wind_kt} kt`;
  $("w-hx").innerHTML = n.headwind_kt == null ? "–" : `${fmt(n.headwind_kt)} / ${fmt(n.crosswind_kt)} kt` + (n.crosswind_alert ? chip("crosswind alert", "bad") : "");
  $("w-qnh").textContent = n.qnh_hpa ? n.qnh_hpa + " hPa" : "–";
  $("w-cf").textContent = n.capacity_factor == null ? "–" : "× " + fmt(n.capacity_factor, 2);
  $("w-metar").textContent = n.raw_metar || "Waiting for METAR observation";
  $("w-time").textContent = n.metar_time ? `METAR observed at ${n.metar_time} IST` : "";

  const h = d.hourly, labels = h.map((r) => hhmm(r.hour_ist));
  drawChart("c-hourly", { type: "line", data: { labels, datasets: [
    { label: "Scheduled", data: h.map((r) => r.scheduled), borderColor: COLORS.sched, backgroundColor: COLORS.sched, borderDash: [4, 3], tension: 0.2, pointRadius: 0, borderWidth: 2 },
    { label: "Actual (used the runway)", data: h.map((r) => r.actual), borderColor: COLORS.ink, backgroundColor: COLORS.ink, tension: 0.2, pointRadius: 2, borderWidth: 2 },
    { label: "Capacity", data: h.map((r) => r.capacity), borderColor: COLORS.bad, borderDash: [6, 4], pointRadius: 0, stepped: true },
  ] }, options: lineOpts("movements per hour") });
  drawChart("c-arrdep", { type: "bar", data: { labels, datasets: [
    { label: "Arrivals", data: h.map((r) => r.arrivals), backgroundColor: COLORS.arr, stack: "s" },
    { label: "Departures", data: h.map((r) => r.departures), backgroundColor: COLORS.dep, stack: "s" },
  ] }, options: { ...lineOpts(), scales: { x: { stacked: true, grid: { display: false }, ticks: { maxTicksLimit: 12 } }, y: { stacked: true, beginAtZero: true } } } });
  drawChart("c-delay", { type: "line", data: { labels, datasets: [
    { label: "Departures", data: h.map((r) => r.avg_dep_delay), borderColor: COLORS.dep, backgroundColor: COLORS.dep, tension: 0.25, pointRadius: 2, spanGaps: true },
    { label: "Arrivals", data: h.map((r) => r.avg_arr_delay), borderColor: COLORS.arr, backgroundColor: COLORS.arr, tension: 0.25, pointRadius: 2, spanGaps: true },
  ] }, options: lineOpts("minutes") });

  const order = ["On time (<15 min)", "15-29 min late", "30-59 min late", "60+ min late", "Cancelled"];
  const bands = order.map((b) => (d.delay_buckets.find((r) => r.delay_bucket === b) || {}).flights || 0);
  drawChart("c-bands", { type: "bar", data: { labels: order, datasets: [{ data: bands, borderWidth: 0,
    backgroundColor: [COLORS.good, "#E8B53A", COLORS.dep, COLORS.bad, COLORS.neutral], borderRadius: 2, barThickness: 22 }] },
    options: { indexAxis: "y", maintainAspectRatio: false, plugins: { legend: { display: false } },
               scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { grid: { display: false } } } } });

  $("upcoming").innerHTML = d.upcoming.map((r) => `<tr><td class="time">${esc(r.expected)}</td><td class="flight">${esc(r.flight)}</td>
    <td><span class="tag ${r.movement === "ARR" ? "arr" : "dep"}">${esc(r.movement)}</span></td><td>${esc(r.from_to)}</td>
    <td>${esc(r.runway)}</td><td class="num ${r.delay_min >= 15 ? "late" : ""}">${fmt(r.delay_min)}</td></tr>`).join("")
    || emptyRow(6, "No movements in the next 2 hours.");

});
