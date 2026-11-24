// Airlines and weather page: refreshes from the same operations snapshot every 30 seconds.
const lineOpts = () => ({
  maintainAspectRatio: false,
  interaction: { mode: "index", intersect: false },
  plugins: { legend: { position: "top", align: "start" } },
  scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } }, y: { beginAtZero: true } },
});

poll("/api/ops", 30, (d) => {
  const n = d.now || {}, wx = d.weather_history || [];
  const airlines = d.airlines || [];
  const cancelled = airlines.reduce((sum, r) => sum + (r.cancelled || 0), 0);

  $("a-otp").textContent = n.otp_24h_pct == null ? "–" : fmt(n.otp_24h_pct, 1) + " %";
  $("a-delay").textContent = n.avg_dep_delay_last_60 == null ? "–" : fmt(n.avg_dep_delay_last_60, 1) + " min";
  $("a-cancel").textContent = fmt(cancelled);
  $("a-vis").textContent = n.vis_km == null ? "–" : fmt(n.vis_km, 1) + " km";
  $("a-metar").textContent = n.metar_time ? `METAR at ${n.metar_time} IST` : "waiting for METAR";
  $("a-wind").textContent = n.wind_kt == null ? "–" : `${n.wind_dir == null ? "Variable" : String(n.wind_dir).padStart(3, "0") + "°"} / ${n.wind_kt} kt`;
  $("a-crosswind").textContent = n.crosswind_alert ? "Crosswind alert" : "";

  $("airlines").innerHTML = airlines.map((r) => `<tr><td>${esc(r.airline)}</td><td class="num">${fmt(r.flights_operated)}</td>
    <td class="num ${r.on_time_pct >= 80 ? "ontime" : r.on_time_pct < 60 ? "late" : ""}">${fmt(r.on_time_pct, 1)} %</td>
    <td class="num">${fmt(r.avg_delay_min, 1)} min</td><td class="num">${fmt(r.cancelled)}</td></tr>`).join("")
    || emptyRow(5, "No airline figures in the last 24 hours.");

  drawChart("c-wx", {
    type: "line",
    data: { labels: wx.map((r) => hhmm(r.obs_ist)), datasets: [
      { label: "Visibility (km)", data: wx.map((r) => r.vis_km), borderColor: COLORS.arr, pointRadius: 0, tension: 0.2 },
      { label: "Wind (kt)", data: wx.map((r) => r.wind_kt), borderColor: COLORS.neutral, pointRadius: 0, tension: 0.2 },
      { label: "Crosswind (kt)", data: wx.map((r) => r.crosswind_kt), borderColor: COLORS.dep, pointRadius: 0, tension: 0.2 },
    ] },
    options: lineOpts(),
  });
});
