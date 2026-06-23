// ESM shim for @deck.gl/mapbox — re-exports named bindings from the accumulated
// globalThis.deck namespace populated by the vendored self-contained UMD bundles
// (deck-core/layers/extensions/mapbox .umd.js), which the HTML loads as classic
// <script> tags (in that order) BEFORE this module evaluates. Zero CDN, no bundler,
// no React: this is a same-origin re-export, not a network fetch.
const D = globalThis.deck;
if (!D || !D.Deck) throw new Error('@deck.gl/mapbox shim: globalThis.deck not populated — load deck-*.umd.js (core first) before the import-map module');
if (!D.MapboxOverlay) throw new Error('@deck.gl/mapbox shim: expected globalThis.deck.MapboxOverlay — bundle load order wrong');
const {
  MapboxOverlay
} = D;
export {
  MapboxOverlay
};
export default D;
