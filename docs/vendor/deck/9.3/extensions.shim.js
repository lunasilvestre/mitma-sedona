// ESM shim for @deck.gl/extensions — re-exports named bindings from the accumulated
// globalThis.deck namespace populated by the vendored self-contained UMD bundles
// (deck-core/layers/extensions/mapbox .umd.js), which the HTML loads as classic
// <script> tags (in that order) BEFORE this module evaluates. Zero CDN, no bundler,
// no React: this is a same-origin re-export, not a network fetch.
const D = globalThis.deck;
if (!D || !D.Deck) throw new Error('@deck.gl/extensions shim: globalThis.deck not populated — load deck-*.umd.js (core first) before the import-map module');
if (!D.DataFilterExtension) throw new Error('@deck.gl/extensions shim: expected globalThis.deck.DataFilterExtension — bundle load order wrong');
const {
  BrushingExtension,
  ClipExtension,
  CollisionFilterExtension,
  DataFilterExtension,
  FillStyleExtension,
  Fp64Extension,
  MaskExtension,
  PathStyleExtension,
  _TerrainExtension,
  project64
} = D;
export {
  BrushingExtension,
  ClipExtension,
  CollisionFilterExtension,
  DataFilterExtension,
  FillStyleExtension,
  Fp64Extension,
  MaskExtension,
  PathStyleExtension,
  _TerrainExtension,
  project64
};
export default D;
