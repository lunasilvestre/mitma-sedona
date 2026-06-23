// ESM shim for lz-string 1.5.0 — re-exports the encode/decode bindings from the
// globalThis.LZString namespace populated by the vendored UMD bundle
// (lz-string.umd.js), which the HTML loads as a classic <script> BEFORE the
// import-map module evaluates. Zero CDN: this is a same-origin re-export, not a
// network fetch. The applet only uses the URL-safe pair.
const L = globalThis.LZString;
if (!L || !L.compressToEncodedURIComponent) {
  throw new Error('lz-string shim: globalThis.LZString not populated — load lz-string.umd.js (classic script) before the import-map module');
}
export const compressToEncodedURIComponent = L.compressToEncodedURIComponent;
export const decompressFromEncodedURIComponent = L.decompressFromEncodedURIComponent;
export default L;
