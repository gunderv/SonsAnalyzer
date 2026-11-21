/* Race analysis page */
(() => {
  let A = null;          // analysis payload
  let T = null;          // track arrays
  let map, boat, trackLayer, chart, idx = 0, timer = null;

  const PCT_STOPS = [[0, "#c0392b"], [85, "#e67e22"], [90, "#e9c22b"], [95, "#7cc35a"], [100, "#1f8a4c"]];
  const MODE_COLORS = { upwind: "#2b83c6", reach: "#3aa55d", downwind: "#e08a1e" };
  const SEQ = ["#3b4cc0", "#5a8de0", "#7fc4e8", "#a5dbb0", "#f2d36b", "#f09a45", "#d6452c"];

  const $ = (s) => document.querySelector(s);

  async function load() {
    try {
      A = await SA.api(`/api/races/${RACE_ID}/analysis`);
    } catch (e) {
      $("#loading").style.display = "none";
      const er = $("#error");
      er.style.display = "block";
      er.innerHTML = `<b>Could not analyse this race.</b><br><span>${SA.esc(e.message)}</span><br><br>
        <span class="muted">Check the time window under Race settings, or that the logger was running.</span>`;
      return;
    }
    T = A.track;
    $("#loading").style.display = "none";
    $("#content").style.display = "block";
    $("#subtitle").textContent = `${SA.dateTime(A.race.start_time)} – ${SA.time(A.race.end_time)}` +
      (A.race.gun_time ? ` · Gun ${SA.time(A.race.gun_time)}` : "") + (A.has_polar ? "" : " · no polar loaded");
    renderTiles();
    initMap();
    initChart();
    renderLegs();
    renderManeuvers();
    renderBands();
    renderStart();
    fillSettings();
    setIndex(0);
  }

  // ------------------------------------------------------------------ tiles
  function renderTiles() {
    const s = A.summary || {};
    const mans = A.maneuvers.filter((m) => !m.prestart);
    const tacks = mans.filter((m) => m.kind === "tack"), gybes = mans.filter((m) => m.kind === "gybe");
    const avg = (arr, k) => { const v = arr.map((m) => m[k]).filter((x) => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
    const tile = (k, v, cls = "") => `<div class="tile"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
    $("#tiles").innerHTML = [
      tile("Duration", SA.dur(s.duration_s)),
      tile("Distance", `${SA.f(s.distance_nm, 1)} <small>nm</small>`),
      tile("Avg TWS", `${SA.f(s.avg_tws, 1)} <small>kn</small>`),
      tile("Avg BSP", `${SA.f(s.avg_bsp, 2)} <small>kn</small>`),
      tile("Polar", `${SA.f(s.avg_polar_pct, 1)}<small>%</small>`, SA.pctClass(s.avg_polar_pct)),
      tile("Upwind VMG", `${SA.f(s.upwind_vmg_pct, 1)}<small>%</small>`, SA.pctClass(s.upwind_vmg_pct)),
      tile("Downwind VMG", `${SA.f(s.downwind_vmg_pct, 1)}<small>%</small>`, SA.pctClass(s.downwind_vmg_pct)),
      tile("Tacks", `${tacks.length} <small>${avg(tacks, "loss_m") != null ? "avg " + SA.f(avg(tacks, "loss_m"), 0) + " m" : ""}</small>`),
      tile("Gybes", `${gybes.length} <small>${avg(gybes, "loss_m") != null ? "avg " + SA.f(avg(gybes, "loss_m"), 0) + " m" : ""}</small>`),
    ].join("");
  }

  // ------------------------------------------------------------------ map
  function lerpColor(stops, v) {
    if (v == null) return "#888";
    let c = stops[0][1];
    for (const [th, col] of stops) if (v >= th) c = col;
    return c;
  }
  function colorFn(metric) {
    if (metric === "mode") return { fn: (i) => MODE_COLORS[T.mode[i]] || "#888",
      legend: Object.entries(MODE_COLORS).map(([k, c]) => [k, c]) };
    if (metric === "polar_pct" || metric === "vmg_pct") {
      return { fn: (i) => lerpColor(PCT_STOPS, T[metric][i]),
        legend: [["<85 %", PCT_STOPS[0][1]], ["85–90", PCT_STOPS[1][1]], ["90–95", PCT_STOPS[2][1]], ["95–100", PCT_STOPS[3][1]], ["≥100 %", PCT_STOPS[4][1]]] };
    }
    const vals = T[metric].filter((v) => v != null).sort((a, b) => a - b);
    const lo = vals[Math.floor(vals.length * 0.03)] ?? 0, hi = vals[Math.floor(vals.length * 0.97)] ?? 1;
    const fn = (i) => {
      const v = T[metric][i];
      if (v == null) return "#888";
      const f = Math.max(0, Math.min(0.999, (v - lo) / (hi - lo || 1)));
      return SEQ[Math.floor(f * SEQ.length)];
    };
    return { fn, legend: [[`${SA.f(lo, 1)} kn`, SEQ[0]], ["", SEQ[2]], ["", SEQ[4]], [`${SA.f(hi, 1)} kn`, SEQ[6]]] };
  }

  function drawTrack() {
    if (trackLayer) trackLayer.remove();
    trackLayer = L.layerGroup().addTo(map);
    const { fn, legend } = colorFn($("#colorBy").value);
    let seg = [], col = null;
    const flush = () => { if (seg.length > 1) L.polyline(seg, { color: col, weight: 4, opacity: 0.95 }).addTo(trackLayer); };
    for (let i = 0; i < T.t.length; i++) {
      if (T.lat[i] == null) { flush(); seg = []; continue; }
      const c = fn(i), p = [T.lat[i], T.lon[i]];
      if (c !== col && seg.length) { seg.push(p); flush(); seg = [p]; } else seg.push(p);
      col = c;
    }
    flush();
    $("#legend").innerHTML = legend.map(([l, c]) => `<span class="sw" style="background:${c}"></span>${SA.esc(l)}`).join(" ");
    // maneuvers
    A.maneuvers.filter((m) => !m.prestart && m.lat != null).forEach((m) => {
      L.circleMarker([m.lat, m.lon], { radius: 5, color: "#fff", weight: 1.5, fillColor: m.kind === "tack" ? "#2b83c6" : "#e08a1e", fillOpacity: 1 })
        .bindTooltip(`${m.kind} ${SA.time(m.time)}<br>loss ${SA.f(m.loss_m, 0)} m, min ${SA.f(m.min_bsp, 1)} kn`)
        .on("click", () => jumpTo(m.time, -15))
        .addTo(trackLayer);
    });
    // start line
    if (A.start && A.start.pin) {
      L.polyline([A.start.pin, A.start.boat], { color: "#d6452c", weight: 3, dashArray: "6 6" }).addTo(trackLayer);
      L.circleMarker(A.start.pin, { radius: 5, color: "#d6452c", fillOpacity: 1 }).bindTooltip("Pin").addTo(trackLayer);
      L.circleMarker(A.start.boat, { radius: 5, color: "#d6452c", fillOpacity: 1 }).bindTooltip("Committee boat").addTo(trackLayer);
    }
  }

  function boatIcon(hdg) {
    return L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13],
      html: `<svg width="26" height="26" viewBox="-13 -13 26 26" style="transform:rotate(${hdg || 0}deg)">
        <path d="M0,-11 L6,8 L0,4 L-6,8 Z" fill="#111" stroke="#fff" stroke-width="1.5"/></svg>` });
  }

  function initMap() {
    map = SA.map("map");
    const pts = T.lat.map((la, i) => (la == null ? null : [la, T.lon[i]])).filter(Boolean);
    const bounds = L.latLngBounds(pts).pad(0.08);
    map.fitBounds(bounds);
    // re-fit once the page layout has settled (mobile layouts change size after first paint)
    setTimeout(() => { map.invalidateSize(); map.fitBounds(bounds); }, 150);
    drawTrack();
    boat = L.marker(pts[0], { icon: boatIcon(0), zIndexOffset: 1000 }).addTo(map);
    $("#colorBy").onchange = drawTrack;
    const slider = $("#slider");
    slider.max = T.t.length - 1;
    slider.oninput = () => setIndex(+slider.value);
    $("#play").onclick = togglePlay;
    document.addEventListener("keydown", (e) => {
      if (e.code === "Space" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) { e.preventDefault(); togglePlay(); }
    });
  }

  function togglePlay() {
    if (timer) { clearInterval(timer); timer = null; $("#play").textContent = "▶"; return; }
    $("#play").textContent = "❚❚";
    let acc = 0;
    timer = setInterval(() => {
      acc += (+$("#speed").value * 0.1) / (T.step_s || 1);
      const step = Math.floor(acc);
      if (step > 0) { acc -= step; setIndex(Math.min(T.t.length - 1, idx + step)); }
      if (idx >= T.t.length - 1) togglePlay();
    }, 100);
  }

  function setIndex(i, fromChart = false) {
    idx = Math.max(0, Math.min(T.t.length - 1, i));
    $("#slider").value = idx;
    $("#clock").textContent = SA.time(new Date(T.t[idx]).toISOString());
    if (T.lat[idx] != null) { boat.setLatLng([T.lat[idx], T.lon[idx]]); boat.setIcon(boatIcon(T.hdg[idx])); }
    const r = (label, v, unit = "", cls = "") => `<span>${label} <b class="${cls}">${v}</b>${unit}</span>`;
    const tws = T.tws[idx], twa = T.twa[idx];
    $("#readout").innerHTML = [
      `<span class="chip ${T.mode[idx] || ""}">${T.mode[idx] || "–"}</span>`,
      r("BSP", SA.f(T.bsp[idx], 2), ` / ${SA.f(T.target_bsp[idx], 2)} kn`),
      r("SOG", SA.f(T.sog[idx], 2), " kn"),
      r("TWS", SA.f(tws, 1), " kn"), r("TWD", SA.f(T.twd[idx], 0), "°"),
      r("TWA", SA.f(twa, 0), `° (target ${SA.f(T.target_twa[idx], 0)}°)`),
      r("VMG", SA.f(T.vmg[idx] != null ? Math.abs(T.vmg[idx]) : null, 2), " kn"),
      r("Polar", SA.f(T.polar_pct[idx], 0), "%", SA.pctClass(T.polar_pct[idx])),
      r("Heel", SA.f(T.heel[idx] != null ? Math.abs(T.heel[idx]) : null, 0), "°"),
    ].join("");
    if (!fromChart && chart) {
      const x = T.t[idx] / 1000;
      const sc = chart.scales.x;
      if (x >= sc.min && x <= sc.max) chart.setCursor({ left: chart.valToPos(x, "x"), top: 20 });
    }
  }

  function jumpTo(iso, offsetS = 0) {
    const t = new Date(iso).getTime() + offsetS * 1000;
    let lo = 0, hi = T.t.length - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (T.t[m] < t) lo = m + 1; else hi = m; }
    setIndex(lo);
    if (T.lat[lo] != null) map.setView([T.lat[lo], T.lon[lo]], Math.max(map.getZoom(), 16));
    document.getElementById("map").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // ------------------------------------------------------------------ chart
  const CHARTS = {
    speed: { series: [["BSP", "bsp", "#2b83c6"], ["Target", "target_bsp", "#8a9aa8", [6, 4]], ["SOG", "sog", "#3aa55d"]], unit: "kn" },
    pct: { series: [["Polar %", "polar_pct", "#2b83c6"], ["VMG %", "vmg_pct", "#e08a1e"]], unit: "%" },
    wind: { series: [["TWS", "tws", "#2b83c6"], ["TWD", "twd", "#9b59b6", null, "deg"]], unit: "kn" },
    angle: { series: [["|TWA|", "abs_twa", "#2b83c6"], ["Target TWA", "target_twa", "#8a9aa8", [6, 4]]], unit: "°" },
    heel: { series: [["Heel", "abs_heel", "#2b83c6"], ["Rudder", "rudder", "#e08a1e"]], unit: "°" },
  };
  function col(key) {
    if (key === "abs_twa") return T.twa.map((v) => (v == null ? null : Math.abs(v)));
    if (key === "abs_heel") return T.heel.map((v) => (v == null ? null : Math.abs(v)));
    return T[key];
  }
  function initChart() {
    $("#chartSel").onchange = buildChart;
    window.addEventListener("resize", () => chart && chart.setSize({ width: chartWidth(), height: 260 }));
    buildChart();
  }
  function chartWidth() { return document.getElementById("chart").clientWidth || 800; }
  function buildChart() {
    if (chart) chart.destroy();
    const def = CHARTS[$("#chartSel").value];
    const xs = T.t.map((t) => t / 1000);
    const data = [xs, ...def.series.map((s) => col(s[1]))];
    const axisColor = SA.css("--muted"), grid = SA.css("--line");
    const scales = { x: { time: true } };
    const axes = [{ stroke: axisColor, grid: { stroke: grid } }, { stroke: axisColor, grid: { stroke: grid }, label: def.unit }];
    const series = [{}];
    def.series.forEach(([label, , color, dash, scale]) => {
      series.push({ label, stroke: color, width: 1.5, dash: dash || undefined, scale: scale || "y", spanGaps: false,
        value: (u, v) => (v == null ? "–" : v.toFixed(1)) });
      if (scale && !axes.find((a) => a.scale === scale)) {
        axes.push({ scale, side: 1, stroke: axisColor, grid: { show: false }, label: "°" });
      }
    });
    chart = new uPlot({
      width: chartWidth(), height: 260, scales, axes, series,
      cursor: { drag: { x: true, y: false } },
      hooks: { setCursor: [(u) => { if (u.cursor.idx != null && u.cursor.event) setIndex(u.cursor.idx, true); }] },
    }, data, document.getElementById("chart"));
    chart.over.addEventListener("mousemove", () => { if (chart.cursor.idx != null) setIndex(chart.cursor.idx, true); });
  }

  // ------------------------------------------------------------------ tables
  function renderLegs() {
    const rows = A.legs.map((l) => `<tr>
      <td class="l">${l.n}</td><td class="l"><span class="chip ${l.mode}">${l.mode}</span></td>
      <td class="l"><a href="#" data-t="${l.start}">${SA.time(l.start)}</a></td><td>${SA.dur(l.duration_s)}</td>
      <td>${SA.f(l.distance_nm, 2)}</td><td>${SA.f(l.avg_tws, 1)}</td><td>${SA.f(l.avg_twd, 0)}°</td>
      <td>${SA.f(l.avg_bsp, 2)}</td><td>${SA.f(l.avg_abs_twa, 0)}°</td><td>${SA.f(l.avg_twa_delta, 1)}°</td>
      <td>${SA.f(l.avg_vmg, 2)}</td>
      <td class="${SA.pctClass(l.avg_polar_pct)}">${SA.f(l.avg_polar_pct, 1)}</td>
      <td class="${SA.pctClass(l.avg_vmg_pct)}">${SA.f(l.avg_vmg_pct, 1)}</td>
      <td>${SA.f(l.avg_heel, 0)}°</td><td>${l.maneuvers}</td></tr>`).join("");
    $("#legsTable").innerHTML = `<thead><tr><th class="l">#</th><th class="l">Leg</th><th class="l">Start</th><th>Time</th>
      <th>Dist nm</th><th>TWS</th><th>TWD</th><th>BSP</th><th>|TWA|</th><th title="Average |TWA| minus target TWA">TWA vs tgt</th>
      <th>VMG</th><th>Polar %</th><th>VMG %</th><th>Heel</th><th>Man.</th></tr></thead><tbody>${rows}</tbody>`;
    $("#legsTable").querySelectorAll("a[data-t]").forEach((a) => a.onclick = (e) => { e.preventDefault(); jumpTo(a.dataset.t); });
  }

  let manSort = { key: "time", dir: 1 };
  function renderManeuvers() {
    const ms = A.maneuver_summary || { by_kind: [], by_band: [] };
    const sumRow = (r, first) => `<tr><td class="l">${first}</td><td>${r.count}</td><td>${SA.f(r.loss_m, 1)}</td>
      <td>${SA.f(r.recover_s, 0)}</td><td>${SA.f(r.speed_drop_pct, 0)}</td></tr>`;
    const head = (first) => `<thead><tr><th class="l">${first}</th><th>Count</th><th>Loss m</th><th>Recover s</th><th>Speed drop %</th></tr></thead>`;
    $("#manSummary").innerHTML = head("Type") + "<tbody>" + (ms.by_kind.map((r) => sumRow(r, r.kind)).join("") ||
      `<tr><td colspan="5" class="empty">No maneuvers</td></tr>`) + "</tbody>";
    $("#manBands").innerHTML = head("Type / TWS") + "<tbody>" + (ms.by_band.map((r) => sumRow(r, `${r.kind} ${r.band} kn`)).join("") ||
      `<tr><td colspan="5" class="empty">–</td></tr>`) + "</tbody>";
    $("#manFilter").onchange = drawManTable;
    $("#showPre").onchange = drawManTable;
    drawManTable();
  }
  function drawManTable() {
    const f = $("#manFilter").value, pre = $("#showPre").checked;
    let rows = A.maneuvers.filter((m) => (f === "all" || m.kind === f) && (pre || !m.prestart));
    rows.sort((a, b) => {
      const x = a[manSort.key], y = b[manSort.key];
      if (x == null) return 1; if (y == null) return -1;
      return (x > y ? 1 : x < y ? -1 : 0) * manSort.dir;
    });
    const cols = [["time", "Time", "l"], ["kind", "Type", "l"], ["direction", "Side", "l"], ["tws", "TWS"], ["entry_bsp", "Entry BSP"],
      ["min_bsp", "Min BSP"], ["speed_drop_pct", "Drop %"], ["entry_twa", "In TWA"], ["exit_twa", "Out TWA"],
      ["turn_deg", "Turn °"], ["recover_s", "Recover s"], ["loss_m", "Loss m"]];
    const th = cols.map(([k, l, c]) => `<th class="sortable ${c || ""}" data-k="${k}">${l}${manSort.key === k ? (manSort.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("");
    const lossCls = (v) => (v == null ? "" : v <= 8 ? "good" : v <= 15 ? "" : v <= 25 ? "warn" : "bad");
    const body = rows.map((m) => `<tr class="clickable" data-t="${m.time}">
      <td class="l">${SA.time(m.time)}${m.prestart ? ' <span class="chip">pre</span>' : ""}</td><td class="l">${m.kind}</td><td class="l">${m.direction.replace("->", " → ")}</td>
      <td>${SA.f(m.tws, 1)}</td><td>${SA.f(m.entry_bsp, 2)}</td><td>${SA.f(m.min_bsp, 2)}</td><td>${SA.f(m.speed_drop_pct, 0)}</td>
      <td>${SA.f(m.entry_twa, 0)}</td><td>${SA.f(m.exit_twa, 0)}</td><td>${SA.f(m.turn_deg, 0)}</td>
      <td>${m.recover_s ?? "–"}</td><td class="${lossCls(m.loss_m)}"><b>${SA.f(m.loss_m, 1)}</b></td></tr>`).join("");
    $("#manTable").innerHTML = `<thead><tr>${th}</tr></thead><tbody>${body || `<tr><td colspan="12" class="empty">No maneuvers</td></tr>`}</tbody>`;
    $("#manTable").querySelectorAll("th[data-k]").forEach((h) => h.onclick = () => {
      manSort = { key: h.dataset.k, dir: manSort.key === h.dataset.k ? -manSort.dir : 1 }; drawManTable();
    });
    $("#manTable").querySelectorAll("tr[data-t]").forEach((r) => r.onclick = () => jumpTo(r.dataset.t, -15));
  }

  function renderBands() {
    const order = { upwind: 0, reach: 1, downwind: 2 };
    const rows = [...A.bands].sort((a, b) => order[a.mode] - order[b.mode] || parseFloat(a.band.replace("<", "0")) - parseFloat(b.band.replace("<", "0")));
    $("#bandsTable").innerHTML = `<thead><tr><th class="l">Mode</th><th class="l">TWS</th><th>Time</th><th>BSP</th><th>Target</th>
      <th>Polar %</th><th>VMG</th><th>Target VMG</th><th>VMG %</th><th>|TWA|</th><th>Target TWA</th><th>Heel</th></tr></thead><tbody>` +
      (rows.map((b) => `<tr><td class="l"><span class="chip ${b.mode}">${b.mode}</span></td><td class="l">${b.band} kn</td>
      <td>${SA.dur(b.seconds)}</td><td>${SA.f(b.bsp, 2)}</td><td>${SA.f(b.target_bsp, 2)}</td>
      <td class="${SA.pctClass(b.polar_pct)}">${SA.f(b.polar_pct, 1)}</td><td>${SA.f(b.vmg, 2)}</td><td>${SA.f(b.target_vmg, 2)}</td>
      <td class="${SA.pctClass(b.vmg_pct)}">${SA.f(b.vmg_pct, 1)}</td><td>${SA.f(b.twa, 0)}°</td><td>${SA.f(b.target_twa, 0)}°</td>
      <td>${SA.f(b.heel, 0)}°</td></tr>`).join("") || `<tr><td colspan="12" class="empty">No data</td></tr>`) + "</tbody>";
  }

  // ------------------------------------------------------------------ start
  function renderStart() {
    const box = $("#startBox");
    const s = A.start;
    if (!A.race.gun_time) {
      box.innerHTML = `<div class="empty">Set the start gun time under <b>Race settings</b> to analyse the start.</div>`; return;
    }
    if (!s) {
      box.innerHTML = `<div class="empty">No start line for this race. Ping the pin and committee boat ends on the
        <a href="/live">Live page</a> before the start, or enter the coordinates under <b>Race settings</b>.</div>`; return;
    }
    if (s.error) { box.innerHTML = `<div class="empty">${SA.esc(s.error)}</div>`; return; }
    const tile = (k, v, cls = "") => `<div class="tile"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
    const late = s.crossed_after_s;
    box.innerHTML = `
      <div class="tiles" style="margin-bottom:1rem">
        ${tile("At the gun", s.over_early ? "OVER" : `${SA.f(s.dist_at_gun_m, 0)} <small>m behind</small>`, s.over_early ? "bad" : s.dist_at_gun_m <= 10 ? "good" : s.dist_at_gun_m <= 30 ? "" : "warn")}
        ${tile("Crossed", late == null ? "–" : `+${SA.f(late, 0)} <small>s</small>`, late == null ? "" : late <= 3 ? "good" : late <= 10 ? "" : "warn")}
        ${tile("Speed at gun", `${SA.f(s.bsp_at_gun, 1)} <small>/ ${SA.f(s.target_bsp, 1)} kn</small>`)}
        ${tile("SOG at gun", `${SA.f(s.sog_at_gun, 1)} <small>kn</small>`)}
        ${tile("Position on line", `${SA.f(s.position_pct, 0)} <small>% from pin</small>`)}
        ${tile("Crossed at", `${SA.f(s.cross_position_pct, 0)} <small>% from pin</small>`)}
        ${tile("Line", `${SA.f(s.line_length_m, 0)} <small>m</small>`)}
        ${tile("Line bias", s.bias_deg == null ? "–" : `${SA.f(s.bias_deg, 0)}° <small>${s.favoured_end} end, ${SA.f(s.favoured_advantage_m, 0)} m</small>`)}
      </div>
      <div class="muted" style="font-size:.85rem;margin-bottom:.4rem">Distance to line (m, behind = positive) and speed, from 3 min before to 2 min after the gun.
        Line source: ${SA.esc(A.line_source || "–")}. TWD at gun ${SA.f(s.twd_at_gun, 0)}°.</div>
      <div id="startChart"></div>`;
    const S = s.series;
    const axisColor = SA.css("--muted"), grid = SA.css("--line");
    new uPlot({
      width: document.getElementById("startBox").clientWidth || 800, height: 240,
      scales: { x: { time: false }, kn: {} },
      axes: [{ stroke: axisColor, grid: { stroke: grid }, label: "seconds from gun" },
             { stroke: axisColor, grid: { stroke: grid }, label: "m" },
             { scale: "kn", side: 1, stroke: axisColor, grid: { show: false }, label: "kn" }],
      series: [{}, { label: "Distance to line", stroke: "#d6452c", width: 2 },
               { label: "SOG", stroke: "#3aa55d", scale: "kn" }, { label: "BSP", stroke: "#2b83c6", scale: "kn" }],
    }, [S.t, S.dist_m, S.sog, S.bsp], document.getElementById("startChart"));
  }

  // ------------------------------------------------------------------ settings
  function fillSettings() {
    const f = $("#editRace"), r = A.race;
    f.name.value = r.name;
    f.start_time.value = SA.toLocalInput(r.start_time);
    f.end_time.value = SA.toLocalInput(r.end_time);
    f.gun_time.value = SA.toLocalInput(r.gun_time);
    ["pin_lat", "pin_lon", "boat_lat", "boat_lon"].forEach((k) => f[k].value = r[k] ?? "");
    f.notes.value = r.notes || "";
    f.onsubmit = async (e) => {
      e.preventDefault();
      const num = (v) => (v.trim() === "" ? null : parseFloat(v.replace(",", ".")));
      try {
        await SA.api(`/api/races/${RACE_ID}`, { method: "PUT", json: {
          name: f.name.value, start_time: SA.fromLocalInput(f.start_time.value), end_time: SA.fromLocalInput(f.end_time.value),
          gun_time: SA.fromLocalInput(f.gun_time.value), pin_lat: num(f.pin_lat.value), pin_lon: num(f.pin_lon.value),
          boat_lat: num(f.boat_lat.value), boat_lon: num(f.boat_lon.value), notes: f.notes.value } });
        location.reload();
      } catch (err) { SA.toast(err.message); }
    };
    $("#deleteRace").onclick = async () => {
      if (!confirm("Delete this race? The logged boat data is not affected.")) return;
      await SA.api(`/api/races/${RACE_ID}`, { method: "DELETE" });
      location.href = "/";
    };
  }

  // tabs
  document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => {
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
    if (b.dataset.tab === "start" && A) renderStart(); // chart needs a visible container for its width
  });

  load();
})();
