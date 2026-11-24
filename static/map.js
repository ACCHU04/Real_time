// Live map: Leaflet on OpenStreetMap tiles; aircraft redrawn every 5 seconds from /api/positions.
// Without internet the tiles cannot load, so the page switches to a small offline base map
// (static/vendor/blr_basemap.js: Natural Earth highways, railways and towns, and the Bengaluru city limit).
const PHASE = { Arriving: COLORS.arr, Departing: COLORS.dep, Overflying: "#7B8794", "On ground": "#4A5561" };
const PLANE = "M12 1.5 L13.6 8.2 L22 12.6 L22 14.6 L13.6 12.4 L13.2 18.6 L16.2 20.8 L16.2 22.4 L12 21.2 L7.8 22.4 L7.8 20.8 L10.8 18.6 L10.4 12.4 L2 14.6 L2 12.6 L10.4 8.2 Z";
const RINGS = [5, 15, 30, 60];
let show = "all", last = null, built = false;

const map = L.map("map", { zoomSnap: 0.25, zoomControl: true, attributionControl: true });

// Basemap layer options: Real Satellite, Light, and Dark Radar
const satelliteTiles = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
  maxZoom: 19,
  attribution: 'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and GIS User Community'
});

const lightTiles = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 18, subdomains: "abcd",
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
});

const darkTiles = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 18, subdomains: "abcd",
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
});

// Default to Real Satellite Map
satelliteTiles.addTo(map);

// Add layer switcher control
L.control.layers({
  "🛰️ Satellite": satelliteTiles,
  "🗺️ Light Map": lightTiles,
  "🌙 Dark Radar": darkTiles
}, null, { position: "topright" }).addTo(map);

const fixed = L.layerGroup().addTo(map);     // rings, runways, approach line
const live = L.layerGroup().addTo(map);      // trails and aircraft, redrawn on each refresh

// ---- offline base map when the tiles do not load ----
let tileLoads = 0, tileErrors = 0, offline = null;
satelliteTiles.on("tileload", () => { tileLoads += 1; });
satelliteTiles.on("tileerror", () => { tileErrors += 1; if (tileErrors >= 4 && tileLoads === 0) useOfflineBasemap(); });
setTimeout(() => { if (tileLoads === 0) useOfflineBasemap(); }, 6000);

function useOfflineBasemap() {
  if (offline || !window.BLR_BASEMAP) return;
  const b = window.BLR_BASEMAP;
  map.removeLayer(tiles);
  map.createPane("base").style.zIndex = 250;
  offline = L.layerGroup().addTo(map);
  const opt = { pane: "base", interactive: false };
  L.geoJSON(b.city, { ...opt, style: { color: "#BFC7D0", weight: 1, fillColor: "#E4E8EC", fillOpacity: 1 } }).addTo(offline);
  L.geoJSON(b.lakes, { ...opt, style: { color: "#B5CDE0", weight: 1, fillColor: "#D6E4F0", fillOpacity: 1 } }).addTo(offline);
  L.geoJSON(b.rail, { ...opt, style: { color: "#A3ABB5", weight: 1.2, dashArray: "5 4" } }).addTo(offline);
  L.geoJSON(b.roads, { ...opt, style: (f) => ({ color: "#B4BCC6", weight: f.properties.type === "Major Highway" ? 2.6 : 1.6 }) }).addTo(offline);
  b.places.features.forEach((f) => {
    const [lon, lat] = f.geometry.coordinates;
    L.circleMarker([lat, lon], { ...opt, radius: 3, color: "#6B7785", weight: 1, fillColor: "#fff", fillOpacity: 1 })
      .bindTooltip(f.properties.name, { permanent: true, direction: "right", offset: [6, 0], className: "place-label" }).addTo(offline);
  });
  map.attributionControl.addAttribution("Offline base map: Natural Earth; Bengaluru city limit: DataMeet");
  const note = $("mapnote");
  note.hidden = false;
  note.textContent = "Map tiles could not load (no internet), so an offline base map is shown.";
}

// ---- fixed layers: range rings, runways, approach line ----
function km(lat0, lon0, bearingDeg, distKm) {
  const b = bearingDeg * Math.PI / 180, cos = Math.cos(lat0 * Math.PI / 180);
  return [lat0 + (distKm * Math.cos(b)) / 111.2, lon0 + (distKm * Math.sin(b)) / (111.2 * cos)];
}

function buildFixed(d) {
  fixed.clearLayers();
  const ap = [d.airport.lat, d.airport.lon];
  RINGS.forEach((r) => {
    L.circle(ap, { radius: r * 1000, color: "#E2E8F0", weight: 1.2, dashArray: r === 60 ? null : "4 5", opacity: 0.8, fill: false, interactive: false }).addTo(fixed);
    L.marker(km(ap[0], ap[1], 45, r), { opacity: 0, interactive: false, keyboard: false })
      .bindTooltip(`${r} km`, { permanent: true, direction: "right", offset: [4, 0], className: "ring-label" }).addTo(fixed);
  });
  for (const rw of d.runways) {
    const ends = (d.runway_ends || {})[rw.runway];
    if (!ends) continue;
    const names = Object.keys(ends);
    // Double line for high contrast: crisp white outline with asphalt core
    L.polyline(names.map((n) => ends[n]), { color: "#FFFFFF", weight: 7, lineCap: "butt", opacity: 0.9, interactive: false }).addTo(fixed);
    L.polyline(names.map((n) => ends[n]), { color: "#1E293B", weight: 3.5, lineCap: "butt", interactive: false }).addTo(fixed);
    names.forEach((n, i) => {
      L.marker(ends[n], { opacity: 0, interactive: false, keyboard: false })
        .bindTooltip(n, { permanent: true, direction: i === 0 ? "left" : "right", offset: [i === 0 ? -6 : 6, 0], className: "rwy-label rwy-end" }).addTo(fixed);
    });
    // Extended centre line, 15 km out, on the side the arrivals come from.
    if (rw.used_for === "ARR" && ends[rw.active_direction]) {
      const thr = ends[rw.active_direction], other = ends[names.find((n) => n !== rw.active_direction)];
      const dLat = thr[0] - other[0], dLon = thr[1] - other[1], len = Math.hypot(dLat, dLon);
      const far = [thr[0] + (dLat / len) * (15 / 111.2), thr[1] + (dLon / len) * (15 / 111.2)];
      L.polyline([thr, far], { color: COLORS.arr, weight: 1.5, dashArray: "8 6", opacity: 0.8, interactive: false }).addTo(fixed);
    }
  }
}

// ---- aircraft ----
function planeIcon(a, faded) {
  const col = PHASE[a.phase] || PHASE.Overflying;
  const html = `<svg width="26" height="26" viewBox="0 0 24 24" style="transform:rotate(${a.track ?? 0}deg);opacity:${faded ? 0.3 : 1}">
    <path d="${PLANE}" fill="${col}" stroke="#fff" stroke-width="1.3" stroke-linejoin="round"/></svg>`;
  return L.divIcon({ html, className: "", iconSize: [26, 26], iconAnchor: [13, 13] });
}

function render(d) {
  last = d;
  if (!built) {
    buildFixed(d);
    map.fitBounds(L.latLng(d.airport.lat, d.airport.lon).toBounds(2 * 46000), { padding: [8, 8] });
    built = true;
  }
  live.clearLayers();
  const visible = (a) => show === "all" || a.phase === show;

  for (const a of d.aircraft) {
    const t = d.trails[a.hex];
    if (t && t.length > 1) L.polyline(t, { color: PHASE[a.phase] || PHASE.Overflying, weight: 2, opacity: visible(a) ? 0.55 : 0.12, interactive: false }).addTo(live);
  }
  // Labels: try a few positions beside each aircraft so they do not cover each other.
  const placed = [], W = 74, H = 30;
  const order = [...d.aircraft].sort((p, q) => (visible(q) - visible(p)) || p.dist_km - q.dist_km);
  for (const a of order) {
    const faded = !visible(a);
    const m = L.marker([a.lat, a.lon], { icon: planeIcon(a, faded), keyboard: false, zIndexOffset: faded ? 0 : 500, title: a.callsign || a.hex });
    const p = map.latLngToContainerPoint([a.lat, a.lon]);
    const tries = [[16, -4, "right"], [16, 22, "right"], [16, -30, "right"], [-16, -4, "left"], [-16, 22, "left"], [16, 48, "right"], [16, -56, "right"]];
    let pick = tries[0];
    for (const t of tries) {
      const x0 = t[2] === "right" ? p.x + t[0] : p.x + t[0] - W, y0 = p.y + t[1] - H / 2;
      if (!placed.some(([x, y]) => Math.abs(x - x0) < W && Math.abs(y - y0) < H)) { pick = t; break; }
    }
    placed.push([pick[2] === "right" ? p.x + pick[0] : p.x + pick[0] - W, p.y + pick[1] - H / 2]);
    m.bindTooltip(`${esc(a.flight_iata || a.callsign || a.hex)}<small>${fmt(a.alt_ft)} ft</small>`,
      { permanent: true, direction: pick[2], offset: [pick[0], pick[1]], className: "ac-label", opacity: faded ? 0.35 : 1 });
    m.addTo(live);
  }

  const s = d.summary, w = d.weather || {};
  $("m-air").textContent = fmt(s.airborne);
  $("m-arr").textContent = fmt(s.arriving);
  $("m-dep").textContent = fmt(s.departing);
  $("m-rwy").textContent = d.runways.map((r) => r.active_direction).join(" and ");
  $("m-wind").textContent = w.wind_kt == null ? "" : `Wind from ${String(w.wind_dir ?? 0).padStart(3, "0")}° at ${w.wind_kt} kt`;

  const arr = d.aircraft.filter((a) => a.phase === "Arriving");
  const dep = d.aircraft.filter((a) => a.phase === "Departing");
  $("t-arr").innerHTML = arr.map((a) => `<tr><td class="flight">${esc(a.flight_iata || a.callsign)}</td><td>${esc(a.city || "–")}</td>
    <td class="num">${fmt(a.dist_km)} km</td><td class="num">${fmt(a.alt_ft)} ft</td><td class="num">${a.minutes_to_land == null ? "–" : a.minutes_to_land < 1 ? "under 1 min" : fmt(a.minutes_to_land) + " min"}</td></tr>`).join("")
    || `<tr><td colspan="5" class="empty">No arrivals within 60 km.</td></tr>`;
  $("t-dep").innerHTML = dep.map((a) => `<tr><td class="flight">${esc(a.flight_iata || a.callsign)}</td><td>${esc(a.city || "–")}</td>
    <td class="num">${fmt(a.dist_km)} km</td><td class="num">${fmt(a.alt_ft)} ft</td><td class="num">+${fmt(a.vrate_fpm)}</td></tr>`).join("")
    || `<tr><td colspan="5" class="empty">No departures within 60 km.</td></tr>`;
  $("sec-arr").classList.toggle("dim", show === "Departing");
  $("sec-dep").classList.toggle("dim", show === "Arriving");
}

document.querySelectorAll(".filters button").forEach((b) => b.addEventListener("click", () => {
  show = b.dataset.show;
  document.querySelectorAll(".filters button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  if (last) render(last);
}));
// Runway end labels only when zoomed in far enough to read them; the airport name otherwise.
function runwayLabels() { $("map").classList.toggle("zoomed-in", map.getZoom() >= 11.5); }   // CSS hides .rwy-end below that
map.on("zoomend", () => { runwayLabels(); if (last) render(last); });
runwayLabels();

poll("/api/positions", 5, render);
