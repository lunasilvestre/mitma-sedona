// ESM shim for maplibre-gl 4.7 — re-exports the named bindings from the
// globalThis.maplibregl namespace populated by the vendored UMD bundle
// (maplibre-gl.js), loaded as a classic <script> BEFORE the import-map module.
// Zero CDN: this is a same-origin re-export, not a network fetch.
const M = globalThis.maplibregl;
if (!M || !M.Map) throw new Error('maplibre-gl shim: globalThis.maplibregl not populated — load maplibre-gl.js (classic script) before the import-map module');
export const { Map, Marker, Popup, NavigationControl, AttributionControl, ScaleControl, LngLat, LngLatBounds, Point, MercatorCoordinate, Evented, addProtocol, removeProtocol, setRTLTextPlugin } = M;
export default M;
