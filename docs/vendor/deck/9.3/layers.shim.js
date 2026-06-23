// ESM shim for @deck.gl/layers — re-exports named bindings from the accumulated
// globalThis.deck namespace populated by the vendored self-contained UMD bundles
// (deck-core/layers/extensions/mapbox .umd.js), which the HTML loads as classic
// <script> tags (in that order) BEFORE this module evaluates. Zero CDN, no bundler,
// no React: this is a same-origin re-export, not a network fetch.
const D = globalThis.deck;
if (!D || !D.Deck) throw new Error('@deck.gl/layers shim: globalThis.deck not populated — load deck-*.umd.js (core first) before the import-map module');
if (!D.ArcLayer) throw new Error('@deck.gl/layers shim: expected globalThis.deck.ArcLayer — bundle load order wrong');
const {
  ArcLayer,
  BitmapLayer,
  ColumnLayer,
  GeoJsonLayer,
  GridCellLayer,
  IconLayer,
  LineLayer,
  PathLayer,
  PointCloudLayer,
  PolygonLayer,
  ScatterplotLayer,
  SolidPolygonLayer,
  TextLayer,
  _MultiIconLayer,
  _TextBackgroundLayer
} = D;
export {
  ArcLayer,
  BitmapLayer,
  ColumnLayer,
  GeoJsonLayer,
  GridCellLayer,
  IconLayer,
  LineLayer,
  PathLayer,
  PointCloudLayer,
  PolygonLayer,
  ScatterplotLayer,
  SolidPolygonLayer,
  TextLayer,
  _MultiIconLayer,
  _TextBackgroundLayer
};
export default D;
