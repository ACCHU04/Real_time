// Flights and weather (public board): refreshes every 30 seconds.
function boardRows(rows, departures) {
  if (!rows.length) return emptyRow(6, "No flights in this window.");
  return rows.map((r) => {
    const late = r.status !== "Cancelled" && r.delay_min >= 15;
    const where = departures ? `${esc(r.terminal)}${r.gate ? " / " + esc(r.gate) : ""}` : esc(r.terminal);
    return `<tr>
      <td class="time">${esc(r.sched_time)}</td><td class="flight">${esc(r.flight)}</td><td>${esc(r.city)}</td>
      <td class="${late ? "late" : "ontime"}">${r.status === "Cancelled" ? "–" : esc(r.expected)}</td>
      <td>${statusText(r.status)}</td><td>${where}</td></tr>`;
  }).join("");
}

poll("/api/public", 30, (d) => {
  const s = d.summary, w = d.weather, a = d.airspace || {};
  $("k-arr").textContent = fmt(s.arrivals_next_60);
  $("k-dep").textContent = fmt(s.departures_next_60);
  $("k-otp").textContent = s.on_time_pct_24h == null ? "–" : fmt(s.on_time_pct_24h, 1) + " %";
  $("k-delayed").textContent = fmt(s.delayed_now);
  $("k-cancel").textContent = fmt(s.cancelled);

  $("arrivals").innerHTML = boardRows(d.arrivals, false);
  $("departures").innerHTML = boardRows(d.departures, true);

  $("wx-temp").textContent = w.temp_c == null ? "–" : fmt(w.temp_c) + " °C";
  const cond = (w.condition || "").toLowerCase();
  const wxKind = /rain|drizzle|shower|thunder/.test(cond) ? "rain" : /fog|mist|haze/.test(cond) ? "fog" : /cloud|overcast/.test(cond) ? "cloud" : "sun";
  $("wx-cond").textContent = w.condition || "–";
  $("wx-icon").dataset.kind = wxKind;
  $("wx-icon").innerHTML = icon(wxKind);
  $("wx-time").textContent = w.observed ? "Observed at " + w.observed + " IST" : "Waiting for observation";
  $("wx-feels").textContent = w.feels_c == null ? "–" : fmt(w.feels_c) + " °C";
  $("wx-rh").textContent = w.rh_pct == null ? "–" : fmt(w.rh_pct) + " %";
  $("wx-wind").textContent = w.wind_kmh == null ? "–" : fmt(w.wind_kmh) + " km/h " + (w.wind_dir || "");
  $("wx-vis").textContent = w.vis_km == null ? "–" : fmt(w.vis_km, 1) + " km";
  const adv = $("wx-adv");
  const advisory = w.advisory || "–";
  const caution = advisory !== "–" && !advisory.startsWith("No weather") && /alert|caution|advisory|thunder|rain|fog|haze|low visibility|strong wind/i.test(advisory);
  adv.textContent = advisory;
  adv.className = "advisory" + (caution ? " alert" : "");

  $("air-total").textContent = fmt(a.airborne);
  $("air-arr").textContent = `${fmt(a.arriving)} approaching to land`;
  $("air-dep").textContent = `${fmt(a.departing)} climbing out after take-off`;

  const statusColor = { Scheduled: COLORS.sched, "En Route": "#5B8DC9", Approaching: COLORS.arr, Delayed: COLORS.warn,
                        Boarding: "#7A6BB5", "Gate Closed": "#A79BD6", Cancelled: COLORS.bad, Taxiing: "#4FA3C7", Landed: COLORS.good };
  drawChart("c-status", {
    type: "bar",
    data: { labels: d.status.map((r) => r.status),
            datasets: [{ data: d.status.map((r) => r.flights), backgroundColor: d.status.map((r) => statusColor[r.status] || COLORS.neutral), borderRadius: 2, barThickness: 16 }] },
    options: { indexAxis: "y", maintainAspectRatio: false, plugins: { legend: { display: false } },
               scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { grid: { display: false } } } },
  });
});
