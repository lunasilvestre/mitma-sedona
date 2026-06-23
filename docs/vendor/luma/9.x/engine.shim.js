// ESM shim for @luma.gl/engine — re-exports the bindings @flowmap.gl/layers needs
// ({ Geometry, Model }) from the accumulated globalThis.luma namespace that the
// vendored self-contained deck-core.umd.js sets at eval time (deck 9.3 bundles
// luma.gl 9 inside it and publishes it as globalThis.luma). This is the SAME
// single deck/luma instance the v1 applet already uses — flowmap reuses it, there
// is NO second deck/luma copy. Zero CDN, no bundler, no React: a same-origin
// re-export, not a network fetch.
//
// LOAD ORDER (load-bearing): the HTML loads deck-core.umd.js (which sets
// globalThis.luma) as a classic <script> BEFORE this module evaluates. The
// import-map points "@luma.gl/engine" here, so @flowmap.gl/layers'
//   import { Geometry, Model } from '@luma.gl/engine'
// resolves to these globals.
const L = globalThis.luma;
if (!L) throw new Error('@luma.gl/engine shim: globalThis.luma not populated — load deck-core.umd.js (which bundles luma.gl 9) before the import-map module');
if (typeof L.Model !== 'function' || typeof L.Geometry !== 'function') {
  throw new Error('@luma.gl/engine shim: expected globalThis.luma.{Geometry,Model} — deck bundle did not expose luma.gl engine classes');
}
const {
  Geometry,
  Model,
  CubeGeometry,
  SphereGeometry,
  BufferTransform,
  TextureTransform,
  GroupNode,
  ModelNode,
  ScenegraphNode
} = L;
export {
  Geometry,
  Model,
  CubeGeometry,
  SphereGeometry,
  BufferTransform,
  TextureTransform,
  GroupNode,
  ModelNode,
  ScenegraphNode
};
export default L;
