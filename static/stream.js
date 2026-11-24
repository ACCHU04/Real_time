// Stream and health page: refreshes from the same operations snapshot every 30 seconds.
const lineOpts = (ytitle) => ({
  maintainAspectRatio: false,
  interaction: { mode: "index", intersect: false },
  plugins: { legend: { position: "top", align: "start" } },
  scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } }, y: { beginAtZero: true, title: { display: !!ytitle, text: ytitle } } },
});

poll("/api/ops", 30, (d) => {
  const f = d.flink || {}, fn = f.now || {}, rolling = f.rolling || [], hourly = f.hourly || [], check = f.check || [];
  const pipeline = d.pipeline || [];
  const maxLag = pipeline.reduce((max, r) => Math.max(max, r.avg_lag_s || 0), 0);
  const deadLetters = pipeline.reduce((sum, r) => sum + (r.dead_letters || 0), 0);

  $("s-mov").textContent = fmt(fn.movements_60);
  $("s-asof").textContent = fn.as_of ? `as of ${fn.as_of} IST` : "waiting for Flink";
  $("s-util").textContent = fn.utilisation_pct == null ? "–" : fmt(fn.utilisation_pct, 1) + " %";
  $("s-lag").textContent = pipeline.length ? fmt(maxLag, 2) : "–";
  $("s-dlq").textContent = fmt(deadLetters);

  $("f-mov").textContent = fmt(fn.movements_60);
  $("f-asof").textContent = fn.as_of ? `As of ${fn.as_of} IST` : "No Flink results yet";
  $("f-util").textContent = fn.utilisation_pct == null ? "–" : fmt(fn.utilisation_pct, 1) + " %";
  $("f-check").innerHTML = check.map((r) => `<tr><td>${esc(r.hour)}</td><td class="num">${fmt(r.flink_movements)}</td>
    <td class="num">${fmt(r.sql_movements)}</td><td class="num ${r.difference === 0 ? "ontime" : "late"}">${fmt(r.difference)} ${r.difference === 0 ? chip("match", "good") : chip("diff", "bad")}</td></tr>`).join("")
    || emptyRow(4, "No Flink results yet.");

  const steps = [...new Set(rolling.map((r) => r.step_ist))];
  const runways = [...new Set(rolling.map((r) => r.runway))];
  drawChart("c-roll", {
    type: "line",
    data: { labels: steps.map(hhmm), datasets: runways.map((rw) => {
      const use = (d.runways.find((r) => r.runway === rw) || {}).used_for;
      const arrivals = use === "ARR";
      return { label: rw + (arrivals ? " (arrivals)" : use === "DEP" ? " (departures)" : ""), borderColor: arrivals ? COLORS.arr : COLORS.dep,
        pointRadius: 0, tension: 0.2, borderWidth: 2,
        data: steps.map((s) => (rolling.find((r) => r.step_ist === s && r.runway === rw) || {}).movements_60 ?? null) };
    })
      .concat([{ label: "Capacity per runway", data: steps.map(() => (d.runways[0] || {}).capacity_per_hour ?? null), borderColor: COLORS.bad, borderDash: [6, 4], pointRadius: 0, borderWidth: 1.5 }]) },
    options: lineOpts("movements in 60 min"),
  });

  const hours = [...new Set(hourly.map((r) => r.hour_ist))];
  const late = (mv) => hours.map((hh) => (hourly.find((r) => r.hour_ist === hh && r.movement === mv) || {}).late_flights ?? 0);
  drawChart("c-late", {
    type: "bar",
    data: { labels: hours.map(hhmm), datasets: [
      { label: "Arrivals", data: late("ARR"), backgroundColor: COLORS.arr, stack: "s" },
      { label: "Departures", data: late("DEP"), backgroundColor: COLORS.dep, stack: "s" },
    ] },
    options: { ...lineOpts(), scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, beginAtZero: true } } },
  });

  $("pipeline").innerHTML = pipeline.map((r) => `<tr><td>${esc(r.consumer)}</td><td>${esc(r.topic)}</td>
    <td class="num">${fmt(r.events)}</td><td class="num">${fmt(r.avg_lag_s, 2)}</td>
    <td class="num">${fmt(r.dead_letters)}</td></tr>`).join("")
    || emptyRow(5, "No pipeline events recorded yet.");
});
