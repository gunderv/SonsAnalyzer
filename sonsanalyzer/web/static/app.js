// Shared helpers
const SA = {
  async api(url, opts = {}) {
    const o = { ...opts };
    if (o.json !== undefined) {
      o.method = o.method || "POST";
      o.headers = { "Content-Type": "application/json" };
      o.body = JSON.stringify(o.json);
      delete o.json;
    }
    const r = await fetch(url, o);
    let data = null;
    try { data = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) throw new Error((data && data.detail) || r.statusText);
    return data;
  },
  f(v, d = 1, unit = "") {
    if (v === null || v === undefined || Number.isNaN(v)) return "–";
    return Number(v).toFixed(d) + unit;
  },
  pctClass(v) {
    if (v == null) return "";
    return v >= 98 ? "good" : v >= 92 ? "" : v >= 85 ? "warn" : "bad";
  },
  time(iso) {
    if (!iso) return "–";
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  },
  dateTime(iso) {
    if (!iso) return "–";
    const d = new Date(iso);
    return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: "numeric" }) +
      " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  },
  dur(s) {
    if (s == null) return "–";
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(sec).padStart(2, "0")}s`;
  },
  // ISO (UTC) -> value for <input type=datetime-local> in the browser's local time
  toLocalInput(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  },
  fromLocalInput(v) { return v ? new Date(v).toISOString() : null; },
  toast(msg, ms = 2500) {
    const t = document.createElement("div");
    t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), ms);
  },
  esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); },
  css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); },
  // Base map with cached OSM + OpenSeaMap tiles (served by the Pi)
  map(el) {
    // Wheel zoom only after the map is clicked, so scrolling the page does not get trapped
    const map = L.map(el, { zoomControl: true, attributionControl: true, preferCanvas: true, scrollWheelZoom: false });
    map.on("click focus", () => map.scrollWheelZoom.enable());
    map.on("mouseout", () => map.scrollWheelZoom.disable());
    L.tileLayer("/tiles/osm/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);
    L.tileLayer("/tiles/seamark/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "OpenSeaMap" }).addTo(map);
    return map;
  },
};
