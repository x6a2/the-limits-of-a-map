// Build data/europe.topo.json from Natural Earth 1:50m admin-0 countries (public domain).
// Only the 22 European countries present in the dataset are selectable; the rest of
// continental Europe is kept as light "context" geometry so the map does not float.
import fs from "node:fs";
import { topology } from "topojson-server";
import { presimplify, simplify } from "topojson-simplify";

const SRC = process.argv[2];
const OUT = process.argv[3];

const EUROPE_22 = {
  GBR: "United Kingdom", IRL: "Ireland", NLD: "Netherlands", DEU: "Germany",
  FRA: "France", ESP: "Spain", CHE: "Switzerland", SWE: "Sweden",
  DNK: "Denmark", BEL: "Belgium", NOR: "Norway", PRT: "Portugal",
  ITA: "Italy", FIN: "Finland", CYP: "Cyprus", AUT: "Austria",
  GRC: "Greece", POL: "Poland", MLT: "Malta", ISL: "Iceland",
  LTU: "Lithuania", CZE: "Czechia",
};
const NAME_FIX = { "Czech Republic": "Czechia", "Republic of Ireland": "Ireland" };
const SKIP = new Set(["RUS", "TUR", "UKR", "BLR", "MDA", "GEO", "ARM", "AZE", "KAZ"]);

// Keep only the European part of each country: drop far-north islands (Svalbard) and
// overseas territories (Greenland, French Guiana, Réunion, Caribbean Netherlands).
const WINDOW = { minLon: -32, maxLon: 45, minLat: 33, maxLat: 73 };
const keepPolygon = (rings) => {
  let minLon = 180, maxLon = -180, minLat = 90, maxLat = -90;
  for (const [lon, lat] of rings[0]) {
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  return maxLon >= WINDOW.minLon && minLon <= WINDOW.maxLon &&
    maxLat >= WINDOW.minLat && minLat <= WINDOW.maxLat;
};
const europeOnly = (geom) => {
  const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.coordinates;
  const keep = polys.filter(keepPolygon);
  if (!keep.length) return null;
  return { type: "MultiPolygon", coordinates: keep };
};

const geo = JSON.parse(fs.readFileSync(SRC, "utf8"));
const feats22 = [];
const featsCtx = [];
for (const f of geo.features) {
  const p = f.properties;
  const iso = p.ISO_A3_EH && p.ISO_A3_EH !== "-99" ? p.ISO_A3_EH : p.ISO_A3;
  const isEur = (p.CONTINENT === "Europe" || p.REGION === "Europe") && !SKIP.has(iso);
  const keepAsContext = p.ADMIN === "Northern Cyprus";
  if (!isEur && !EUROPE_22[iso] && !keepAsContext) continue;
  if (!f.geometry) continue;
  const geom = europeOnly(f.geometry);
  if (!geom) continue;
  const name = EUROPE_22[iso] || NAME_FIX[p.ADMIN] || p.ADMIN;
  const feat = { type: "Feature", properties: { iso, name }, geometry: geom };
  if (EUROPE_22[iso]) feats22.push(feat);
  else featsCtx.push(feat);
}
console.log("selected", feats22.length, "context", featsCtx.length);
console.log("selected isos", feats22.map((f) => f.properties.iso).sort().join(","));

const raw = topology({
  countries: { type: "FeatureCollection", features: feats22 },
  context: { type: "FeatureCollection", features: featsCtx },
});
const simplified = simplify(presimplify(raw), 0.04);

// Quantize to 1e5 (≈1 m at this scale) and delta-encode arcs as the TopoJSON spec wants.
const Q = 1e5;
const [x0, y0, x1, y1] = simplified.bbox;
const scale = [(x1 - x0) / Q, (y1 - y0) / Q];
const qx = (x) => Math.round((x - x0) / scale[0]);
const qy = (y) => Math.round((y - y0) / scale[1]);
const arcs = simplified.arcs.map((arc) => {
  let px = 0, py = 0;
  return arc.map(([x, y], i) => {
    const ix = qx(x), iy = qy(y);
    const out = i === 0 ? [ix, iy] : [ix - px, iy - py];
    px = ix; py = iy;
    return out;
  });
});
simplified.arcs = arcs;
simplified.transform = { scale, translate: [x0, y0] };
for (const coll of Object.values(simplified.objects)) {
  for (const g of coll.geometries) delete g.bbox;
}
fs.writeFileSync(OUT, JSON.stringify(simplified));
console.log("wrote", OUT, (fs.statSync(OUT).size / 1024).toFixed(0) + " KB",
  "arcs", arcs.length, "bbox", simplified.bbox);
