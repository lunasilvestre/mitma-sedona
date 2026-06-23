// OD-Flows Explorer — FLOWMAP variant (Phase 3: full comparison build).
//
// The A/B twin of the v1 ArcLayer applet (docs/app/flows/main.js). It loads the
// SAME 8000 real OD corridors per slice, but renders them with
// @flowmap.gl/layers@9.4 + @flowmap.gl/data@9.4 — a FlowmapLayer (a deck.gl
// CompositeLayer) with the headline flowmap features the v1 hand-rolls or skips:
//   * clusteringEnabled  — zoom-LOD super-nodes (THE differentiator vs v1)
//   * flowLinesRenderingMode:'animated-straight' — animated flow particles
//     (9.4's enum that supersedes the deprecated animationEnabled flag)
//   * adaptiveScalesEnabled + colorScheme + darkMode — FRAMEWORK width/colour
//     scales (not a hand-rolled log width; the library owns the scale domains)
//   * locationTotalsEnabled — node circles sized by their in/out totals
//   * maxTopFlowsDisplayNum — a top-N lever (the flowmap analog of v1's volume cut)
//   * pickable + onHover — typed picking (flow vs location/cluster super-node)
//
// No build / no bundler / no React / no CDN: every bare specifier below resolves
// through the page's <script type=importmap> to a same-origin file under
// docs/vendor/. @deck.gl/core, @deck.gl/layers and @luma.gl/engine map to SHIMS
// that re-export globalThis.deck / globalThis.luma — the exact globals the vendored
// deck-*.umd.js classic <script>s populated. So the FlowmapLayer is constructed from
// the SAME deck/luma the camera Deck uses (one instance, no second deck copy).
//
// REACTIVITY — read honestly: flowmap re-aggregates its zoom-LOD clustering whenever
// the flows table (or the viewport) changes, which is far heavier than the v1
// ArcLayer's single uniform/attribute slide. So every data-mutating lever (hour
// scrubber, slice switch) DEBOUNCES ~250ms and swaps the provider's `data` prop.
// Camera pan/zoom re-clusters too (clustering is zoom-dependent) — that is inherent
// to flowmap and is the price of the super-nodes. The panel caption says so.

import { Deck } from '@deck.gl/core';
import { Map as MapLibreMap, AttributionControl } from 'maplibre-gl';
import { FlowmapLayer } from '@flowmap.gl/layers';

// ---- Keyless basemap registry (matches the v1 applet + the live shell) ----
const ESRI_ATTRIB =
  'Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community';
const CARTO_ATTRIB = '© OpenStreetMap contributors © CARTO';
const CARTO_VOYAGER = 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json';

function esriSatelliteStyle() {
  return {
    version: 8,
    sources: {
      'esri-satellite': {
        type: 'raster',
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
        tileSize: 256, maxzoom: 19, attribution: ESRI_ATTRIB
      }
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#06080d' } },
      { id: 'esri-satellite', type: 'raster', source: 'esri-satellite' }
    ]
  };
}

// `style` is either a MapLibre style object (satellite) or a keyless style-JSON URL.
// The default is DARK (this variant keeps the dark-UI chrome as its primary look).
const BASEMAPS = {
  dark:      { label: 'Dark', style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json', attribution: CARTO_ATTRIB },
  satellite: { label: 'Satellite', style: esriSatelliteStyle, attribution: ESRI_ATTRIB },
  light:     { label: 'Light', style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json', attribution: CARTO_ATTRIB },
  osm:       {
    label: 'OSM', style: 'https://tiles.openfreemap.org/styles/bright',
    attribution: '© OpenStreetMap contributors © OpenFreeMap',
    fallbackStyle: CARTO_VOYAGER, fallbackAttribution: CARTO_ATTRIB
  }
};
const DEFAULT_BASEMAP = 'dark';

// ---- Catalonia view (same framing as the v1 default) ----
const INITIAL_VIEW_STATE = {
  longitude: 2.0,
  latitude: 41.65,
  zoom: 7.4,
  pitch: 0,
  bearing: 0
};
// A camera over the Barcelona metro (for the zoomed-in expanded-flows preset).
const BCN_CAMERA = { longitude: 2.17, latitude: 41.39, zoom: 9.4, pitch: 0, bearing: 0 };

// ---- Flow store (same-origin under docs/story_data/flows_fm/) ----
const FLOW_STORE = './story_data/flows_fm';
const INDEX_URL = `${FLOW_STORE}/flows_fm_index.json`;
const LOCATIONS_URL = `${FLOW_STORE}/locations.json`;
const DEFAULT_SLICE = 'may_weekday';
const DEFAULT_TOPN = 5000;

// ---- The four curated default views (adapted from v1 §6.1) ----
// Each preset is a full lever-state bundle: { w, d, h, topn, cluster, bm, cam }.
// cam = [lon, lat, zoom]. Clicking one calls _applyState(preset).
const DEFAULT_VIEWS = [
  {
    id: 'daily-pulse',
    title: 'Daily pulse',
    blurb: 'Catalonia-wide AM commute — May weekday, 08:00, clustered.',
    state: { w: 'may', d: 'weekday', h: 8, topn: 5000, cluster: true, bm: 'dark',
      cam: [INITIAL_VIEW_STATE.longitude, INITIAL_VIEW_STATE.latitude, INITIAL_VIEW_STATE.zoom] }
  },
  {
    id: 'rush-hour',
    title: 'Rush hour (BCN)',
    blurb: 'Barcelona metro, expanded flows + particles — May weekday, 08:00.',
    state: { w: 'may', d: 'weekday', h: 8, topn: 5000, cluster: true, bm: 'dark',
      cam: [BCN_CAMERA.longitude, BCN_CAMERA.latitude, BCN_CAMERA.zoom] }
  },
  {
    id: 'weekend-leisure',
    title: 'Weekend vs weekday',
    blurb: 'Coastal & leisure corridors — June weekend, 12:00.',
    state: { w: 'jun', d: 'weekend', h: 12, topn: 5000, cluster: true, bm: 'dark',
      cam: [INITIAL_VIEW_STATE.longitude, INITIAL_VIEW_STATE.latitude, INITIAL_VIEW_STATE.zoom] }
  },
  {
    id: 'long-haul',
    title: 'Long-haul corridors',
    blurb: 'The inter-city skeleton — May weekday, 08:00, top-1200 only.',
    state: { w: 'may', d: 'weekday', h: 8, topn: 1200, cluster: true, bm: 'dark',
      cam: [INITIAL_VIEW_STATE.longitude, INITIAL_VIEW_STATE.latitude, 7.2] }
  }
];

// Debounce window for the data-mutating levers. Flowmap re-clusters on each change,
// so we settle generously (the honesty caption names this latency).
const LEVER_DEBOUNCE_MS = 250;

class FlowmapApp {
  constructor() {
    this._viewState = { ...INITIAL_VIEW_STATE };

    // Slice key = `${window}_${daytype}`.
    this._sliceKey = DEFAULT_SLICE;
    const [defWindow, defDaytype] = DEFAULT_SLICE.split('_');
    this._window = defWindow;       // 'feb' | 'may' | 'jun'
    this._daytype = defDaytype;     // 'weekday' | 'weekend'

    this._activeHour = 8;
    this._topN = DEFAULT_TOPN;
    this._clusteringEnabled = true;
    this._basemapKey = DEFAULT_BASEMAP;
    this._basemapFellBack = false;

    this._index = null;
    this._locations = null;         // [{ id, lon, lat }]
    // Raw flows for the active slice, as loaded: [{ origin, dest, count, hourly[24] }].
    this._rawFlows = null;
    // The flowmap `data` object: { locations, flows } where flows[].count = the
    // weight at the active hour. We rebuild flows.count from hourly[h] on a scrub.
    this._data = null;
    this._hover = null;

    // Debounce + token state for the data-mutating levers.
    this._leverDebounce = null;     // setTimeout handle (hour + slice swaps)
    this._sliceLoadToken = 0;       // monotonic — drops stale in-flight slice loads

    // Hour-animation play/pause loop (steps 0..23, loops).
    this._animTimer = null;
    this._animating = false;
  }

  async start() {
    this._buildBasemap();
    this._buildDeck();
    this._buildViews();
    this._buildSliceSwitcher();
    this._buildScrubber();
    this._buildTopN();
    this._buildClusteringToggle();
    this._buildBasemapSwitch();
    this._buildPanelToggle();
    // Async: load the locations + the default slice, then render. window.__fmReady
    // flips true once the first FlowmapLayer is on the deck.
    await this._boot();
    window.__fm = this;
  }

  // ---- Basemap (passive host; deck owns the camera) -----------------------

  _resolveStyle(key) {
    const bm = BASEMAPS[key] || BASEMAPS[DEFAULT_BASEMAP];
    return typeof bm.style === 'function' ? bm.style() : bm.style;
  }

  _buildBasemap() {
    const bm = BASEMAPS[this._basemapKey] || BASEMAPS[DEFAULT_BASEMAP];
    const map = new MapLibreMap({
      container: 'map',
      style: this._resolveStyle(this._basemapKey),
      center: [this._viewState.longitude, this._viewState.latitude],
      zoom: this._viewState.zoom,
      pitch: this._viewState.pitch,
      bearing: this._viewState.bearing,
      attributionControl: false,
      interactive: false
    });
    this._attribCtrl = new AttributionControl({ compact: true, customAttribution: bm.attribution || CARTO_ATTRIB });
    map.addControl(this._attribCtrl, 'bottom-right');
    map.on('error', () => this._maybeBasemapFallback());
    this.map = map;
  }

  _maybeBasemapFallback() {
    const bm = BASEMAPS[this._basemapKey];
    if (!bm || !bm.fallbackStyle || this._basemapFellBack) return;
    this._basemapFellBack = true;
    try {
      this.map.setStyle(bm.fallbackStyle);
      this._updateAttribution(bm.fallbackAttribution || CARTO_ATTRIB);
    } catch { /* leave the current style */ }
  }

  _updateAttribution(text) {
    if (!this.map) return;
    try { if (this._attribCtrl) this.map.removeControl(this._attribCtrl); }
    catch { /* control may already be gone after setStyle */ }
    this._attribCtrl = new AttributionControl({ compact: true, customAttribution: text });
    this.map.addControl(this._attribCtrl, 'bottom-right');
  }

  _buildBasemapSwitch() {
    const self = this;
    const sel = document.getElementById('basemap-select');
    const hint = document.getElementById('basemap-hint');
    if (!sel) return;
    this._basemapEls = { sel, hint };
    sel.value = this._basemapKey;
    sel.addEventListener('change', () => self._setBasemap(sel.value));
  }

  _setBasemap(key) {
    if (!BASEMAPS[key] || key === this._basemapKey) return;
    this._basemapKey = key;
    this._basemapFellBack = false;
    const bm = BASEMAPS[key];
    try {
      this.map.setStyle(this._resolveStyle(key));
      this._updateAttribution(bm.attribution || CARTO_ATTRIB);
    } catch (err) {
      console.error('[flows_fm] basemap switch failed:', err && err.message ? err.message : err);
    }
    const hint = this._basemapEls && this._basemapEls.hint;
    if (hint) hint.textContent = `${bm.label} — keyless. The flows stay on top.`;
    this._markActiveView();
  }

  // ---- deck.gl camera (the sole authority; passive map follows) -----------

  _buildDeck() {
    const self = this;
    this.deck = new Deck({
      canvas: document.getElementById('deck-canvas'),
      width: '100%',
      height: '100%',
      initialViewState: this._viewState,
      controller: true,
      onViewStateChange: ({ viewState }) => {
        self._viewState = viewState;
        if (self.map) {
          self.map.jumpTo({
            center: [viewState.longitude, viewState.latitude],
            zoom: viewState.zoom,
            bearing: viewState.bearing,
            pitch: viewState.pitch
          });
        }
        // Clustering is a function of zoom — re-render so the LOD super-nodes
        // re-form. (Flowmap also re-aggregates internally on viewportChanged.)
        self._render();
        // A manual camera move drifts off any lit preset.
        self._markActiveView();
      },
      // The FlowmapLayer surfaces a typed picked object; getTooltip reads it.
      getTooltip: (info) => self._tooltipFor(info && info.object)
    });
  }

  // ---- Default views ------------------------------------------------------

  _buildViews() {
    const self = this;
    const list = document.getElementById('view-list');
    const hint = document.getElementById('views-hint');
    this._viewEls = { list, hint, buttons: [] };
    if (!list) return;
    for (const view of DEFAULT_VIEWS) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'view-btn';
      btn.dataset.viewId = view.id;
      btn.setAttribute('aria-pressed', 'false');
      btn.innerHTML = `<strong>${view.title}</strong><span>${view.blurb}</span>`;
      btn.addEventListener('click', () => {
        self._applyState(view.state).then(() => self._markActiveView(view.id));
        if (hint) hint.textContent = `Showing “${view.title}”.`;
      });
      list.appendChild(btn);
      this._viewEls.buttons.push(btn);
    }
  }

  // Mark the active preset (aria-pressed) on the structural levers (slice + hour +
  // top-N + clustering + basemap), not the camera, so a small pan keeps it lit.
  _markActiveView(id) {
    const els = this._viewEls;
    if (!els || !els.buttons) return;
    let activeId = id || null;
    if (!activeId) {
      const m = DEFAULT_VIEWS.find((v) =>
        v.state.w === this._window && v.state.d === this._daytype &&
        v.state.h === this._activeHour && v.state.topn === this._topN &&
        !!v.state.cluster === this._clusteringEnabled && v.state.bm === this._basemapKey);
      activeId = m ? m.id : null;
    }
    for (const b of els.buttons) {
      b.setAttribute('aria-pressed', b.dataset.viewId === activeId ? 'true' : 'false');
    }
  }

  // Apply a full preset bundle to the LIVE app: slice (re-fetch if changed), hour,
  // top-N, clustering, basemap, camera — then one render. Stops any animation.
  async _applyState(s) {
    if (!s) return false;
    this._stopAnimate();
    const key = `${s.w}_${s.d}`;
    // Basemap (passive host only — never the camera).
    if (s.bm && s.bm !== this._basemapKey) this._setBasemap(s.bm);
    // Levers (no render yet — we coalesce into one below).
    this._activeHour = Math.max(0, Math.min(23, (s.h | 0) || 0));
    this._topN = Math.max(1, (s.topn | 0) || DEFAULT_TOPN);
    this._clusteringEnabled = !!s.cluster;
    // Slice (data swap only if it actually changed).
    if (key !== this._sliceKey) {
      const token = ++this._sliceLoadToken;
      try { await this._loadSlice(key); }
      catch (err) {
        if (token !== this._sliceLoadToken) return false;
        console.error('[flows_fm] preset slice load failed:', err && err.message ? err.message : err);
        return false;
      }
      if (token !== this._sliceLoadToken) return false;
      this._sliceKey = key;
      this._window = s.w; this._daytype = s.d;
    } else {
      this._window = s.w; this._daytype = s.d;
    }
    // Rebuild flows.count for the applied hour.
    this._rebuildFlowsForHour();
    // Camera (deck is the authority; jumpTo the passive map to match).
    if (Array.isArray(s.cam) && s.cam.length >= 3) {
      this._viewState = {
        ...this._viewState,
        longitude: s.cam[0], latitude: s.cam[1], zoom: s.cam[2], pitch: 0, bearing: 0
      };
      if (this.deck) this.deck.setProps({ initialViewState: this._viewState });
      if (this.map) this.map.jumpTo({ center: [s.cam[0], s.cam[1]], zoom: s.cam[2], pitch: 0, bearing: 0 });
    }
    this._syncControlsToState();
    this._render();
    return true;
  }

  // ---- Slice switcher -----------------------------------------------------

  _buildSliceSwitcher() {
    const self = this;
    const winSel = document.getElementById('window-select');
    const dayRadios = Array.from(document.querySelectorAll('#daytype-toggle input[name="daytype"]'));
    const hint = document.getElementById('slice-hint');
    if (!winSel || dayRadios.length === 0) return;
    this._sliceEls = { winSel, dayRadios, hint };
    winSel.value = this._window;
    for (const r of dayRadios) r.checked = (r.value === this._daytype);

    winSel.addEventListener('change', () => { self._window = winSel.value; self._requestSlice(); });
    for (const r of dayRadios) {
      r.addEventListener('change', () => {
        if (!r.checked) return;
        self._daytype = r.value;
        self._requestSlice();
      });
    }
  }

  // Debounce the slice swap (flowmap re-clusters on the data change).
  _requestSlice() {
    this._stopAnimate();
    const key = `${this._window}_${this._daytype}`;
    const hint = this._sliceEls && this._sliceEls.hint;
    if (hint) hint.innerHTML = `Loading slice <code>${key}</code>…`;
    if (this._leverDebounce) clearTimeout(this._leverDebounce);
    this._leverDebounce = setTimeout(() => { this._setSlice(key); }, LEVER_DEBOUNCE_MS);
  }

  // Commit a slice switch — a DATA-PROP SWAP. A monotonic token drops a stale
  // in-flight load if the user switches again mid-fetch.
  async _setSlice(key) {
    if (key === this._sliceKey) return;
    const token = ++this._sliceLoadToken;
    const hint = this._sliceEls && this._sliceEls.hint;
    try {
      await this._loadSlice(key);
    } catch (err) {
      if (token !== this._sliceLoadToken) return; // superseded
      console.error('[flows_fm] slice switch failed:', err && err.message ? err.message : err);
      if (hint) hint.innerHTML = `Slice <code>${key}</code> unavailable.`;
      return;
    }
    if (token !== this._sliceLoadToken) return; // a newer switch landed
    this._sliceKey = key;
    const [win, day] = key.split('_');
    this._window = win; this._daytype = day;
    this._rebuildFlowsForHour();
    this._render();
    if (hint) {
      const n = (this._rawFlows ? this._rawFlows.length : 0).toLocaleString();
      hint.innerHTML = `Slice <code>${key}</code> — ${n} corridors.`;
    }
    this._markActiveView();
  }

  // ---- Hour scrubber ------------------------------------------------------

  _buildScrubber() {
    const self = this;
    const range = document.getElementById('hour-range');
    const label = document.getElementById('hour-label');
    const hint = document.getElementById('hour-hint');
    const playBtn = document.getElementById('hour-play');
    if (!range || !label) return;
    this._scrubEls = { range, label, hint, playBtn };

    const fmt = (h) => `${String(h).padStart(2, '0')}:00`;
    range.value = String(this._activeHour);
    label.textContent = fmt(this._activeHour);
    range.setAttribute('aria-valuetext', fmt(this._activeHour));

    const onScrub = (commitNow) => {
      const h = Math.max(0, Math.min(23, parseInt(range.value, 10) || 0));
      label.textContent = fmt(h);
      range.setAttribute('aria-valuetext', fmt(h));
      if (self._leverDebounce) { clearTimeout(self._leverDebounce); self._leverDebounce = null; }
      if (commitNow) self._setActiveHour(h);
      else self._leverDebounce = setTimeout(() => self._setActiveHour(h), LEVER_DEBOUNCE_MS);
    };
    range.addEventListener('input', () => { self._stopAnimate(); onScrub(false); });
    range.addEventListener('change', () => { self._stopAnimate(); onScrub(true); });
    if (playBtn) playBtn.addEventListener('click', () => self._toggleAnimate());
  }

  // Commit an hour: rebuild every flow's count = hourly[h], swap the provider data,
  // re-render (re-clusters). This is the heavier, debounced path vs v1.
  _setActiveHour(h) {
    this._activeHour = h;
    const hint = this._scrubEls && this._scrubEls.hint;
    this._rebuildFlowsForHour();
    this._render();
    if (hint) hint.textContent = "Each flow's weight = trips in this hour. Re-clusters on a ~250 ms debounce.";
    this._markActiveView();
  }

  // Play/pause animation: steps 0..23 on a timer, loops. Slower than v1 (700ms→900ms)
  // because each step re-clusters; legible without thrashing the aggregation.
  _toggleAnimate() {
    if (this._animating) { this._stopAnimate(); return; }
    this._animating = true;
    this._reflectPlayState();
    this._animTimer = setInterval(() => {
      const next = (this._activeHour + 1) % 24;
      if (this._scrubEls && this._scrubEls.range) {
        this._scrubEls.range.value = String(next);
        this._scrubEls.label.textContent = `${String(next).padStart(2, '0')}:00`;
        this._scrubEls.range.setAttribute('aria-valuetext', `${String(next).padStart(2, '0')}:00`);
      }
      this._setActiveHour(next);
    }, 900);
  }

  _stopAnimate() {
    if (!this._animating) return;
    this._animating = false;
    if (this._animTimer) { clearInterval(this._animTimer); this._animTimer = null; }
    this._reflectPlayState();
  }

  _reflectPlayState() {
    const btn = this._scrubEls && this._scrubEls.playBtn;
    if (!btn) return;
    btn.setAttribute('aria-pressed', this._animating ? 'true' : 'false');
    btn.textContent = this._animating ? '⏸ Pause' : '▶ Play';
    btn.setAttribute('aria-label', this._animating ? 'Pause the hour animation' : 'Play the hour animation');
  }

  // ---- Top-N lever (maxTopFlowsDisplayNum) --------------------------------

  _buildTopN() {
    const self = this;
    const range = document.getElementById('topn-range');
    const label = document.getElementById('topn-label');
    const hint = document.getElementById('topn-hint');
    if (!range || !label) return;
    this._topnEls = { range, label, hint };
    range.value = String(this._topN);
    label.textContent = String(this._topN);

    range.addEventListener('input', () => {
      const v = Math.max(1, parseInt(range.value, 10) || DEFAULT_TOPN);
      self._topN = v;
      label.textContent = v.toLocaleString();
      // maxTopFlowsDisplayNum is a layer prop, not a data change — but flowmap still
      // re-selects the top set, so debounce lightly to avoid thrash on a fast drag.
      if (self._topnDebounce) clearTimeout(self._topnDebounce);
      self._topnDebounce = setTimeout(() => { self._render(); self._markActiveView(); }, 120);
    });
  }

  // ---- Clustering toggle (the headline differentiator) --------------------

  _buildClusteringToggle() {
    const self = this;
    const box = document.getElementById('clustering-toggle');
    const hint = document.getElementById('clustering-hint');
    if (!box) return;
    this._clusterEls = { box, hint };
    box.checked = this._clusteringEnabled;
    box.addEventListener('change', () => {
      self._clusteringEnabled = box.checked;
      if (hint) {
        hint.textContent = box.checked
          ? 'The headline flowmap feature: clusters re-form as you zoom.'
          : 'Clustering off — every flow drawn ungrouped (the hairball).';
      }
      self._render();
      self._markActiveView();
    });
  }

  // ---- Hover readout ------------------------------------------------------

  // Build a tooltip string from a typed picked object (or null). FLOW => origin→dest
  // + trips; LOCATION (incl. a cluster super-node) => id + in/out totals.
  _tooltipFor(obj) {
    if (!obj) return null;
    if (obj.type === 'flow') {
      const o = obj.origin && (obj.origin.id ?? obj.origin) ;
      const d = obj.dest && (obj.dest.id ?? obj.dest);
      return { text: `${o} → ${d}\n${Math.round(obj.count).toLocaleString()} trips/h` };
    }
    if (obj.type === 'location') {
      const t = obj.totals || {};
      const inc = Math.round((t.incomingCount || 0) + (t.internalCount || 0));
      const out = Math.round((t.outgoingCount || 0) + (t.internalCount || 0));
      const isCluster = typeof obj.id === 'string' && /cluster|:/.test(obj.id);
      const head = isCluster ? `Cluster ${obj.id}` : `Node ${obj.name || obj.id}`;
      return { text: `${head}\nin ${inc.toLocaleString()} · out ${out.toLocaleString()}` };
    }
    return null;
  }

  // Mirror the hovered object into the panel readout (richer than the floating tip).
  _updatePickReadout(obj) {
    const el = document.getElementById('pick-readout');
    if (!el) return;
    if (!obj) { el.textContent = 'Hover a flow or a node to inspect it.'; return; }
    if (obj.type === 'flow') {
      const o = obj.origin && (obj.origin.id ?? obj.origin);
      const d = obj.dest && (obj.dest.id ?? obj.dest);
      el.innerHTML = `<strong>Flow</strong> ${o} → ${d}<br>${Math.round(obj.count).toLocaleString()} trips/hour`;
    } else if (obj.type === 'location') {
      const t = obj.totals || {};
      const inc = Math.round((t.incomingCount || 0) + (t.internalCount || 0));
      const out = Math.round((t.outgoingCount || 0) + (t.internalCount || 0));
      const isCluster = typeof obj.id === 'string' && /cluster|:/.test(obj.id);
      el.innerHTML = `<strong>${isCluster ? 'Cluster' : 'Node'}</strong> ${obj.name || obj.id}<br>in ${inc.toLocaleString()} · out ${out.toLocaleString()} trips/hour`;
    }
  }

  // ---- Data load ----------------------------------------------------------

  async _boot() {
    this._index = await fetch(INDEX_URL).then((r) => {
      if (!r.ok) throw new Error(`flows_fm_index.json HTTP ${r.status}`);
      return r.json();
    });
    this._locations = await fetch(LOCATIONS_URL).then((r) => {
      if (!r.ok) throw new Error(`locations.json HTTP ${r.status}`);
      return r.json();
    });
    await this._loadSlice(this._sliceKey);
    this._rebuildFlowsForHour();
    this._render();
    if (this._sliceEls && this._sliceEls.hint) {
      const n = (this._rawFlows ? this._rawFlows.length : 0).toLocaleString();
      this._sliceEls.hint.innerHTML = `Slice <code>${this._sliceKey}</code> — ${n} corridors.`;
    }
    this._markActiveView();
    window.__fmReady = true;
  }

  // Fetch one slice's flows table: [{ origin, dest, count, hourly[24] }].
  async _loadSlice(key) {
    const slice = (this._index.slices || []).find((s) => s.key === key);
    if (!slice) throw new Error(`slice '${key}' not in flows_fm_index.json`);
    const url = `${FLOW_STORE}/${slice.flows_url}`;
    const flows = await fetch(url).then((r) => {
      if (!r.ok) throw new Error(`${slice.flows_url} HTTP ${r.status}`);
      return r.json();
    });
    this._slice = slice;
    this._rawFlows = flows;
  }

  // Rebuild the flowmap `data` object for the active hour. We build a FRESH flows
  // array each time (new object identity) so the FlowmapLayer's dataChanged path
  // fires and the provider re-aggregates. count = hourly[h] (the per-hour weight);
  // both endpoints already resolve to a centroid by construction (export invariant).
  _rebuildFlowsForHour() {
    if (!this._rawFlows || !this._locations) { this._data = null; return; }
    const h = this._activeHour;
    const flows = new Array(this._rawFlows.length);
    for (let i = 0; i < this._rawFlows.length; i++) {
      const f = this._rawFlows[i];
      const hr = f.hourly;
      const c = hr && hr.length === 24 ? hr[h] : f.count;
      flows[i] = { origin: f.origin, dest: f.dest, count: c };
    }
    // A new object identity for `data` so FlowmapLayer treats it as dataChanged.
    this._data = { locations: this._locations, flows };
  }

  // ---- The FlowmapLayer ---------------------------------------------------

  _buildFlowmapLayer() {
    const self = this;
    return new FlowmapLayer({
      id: 'od-flowmap',
      // FlowmapLayer recognises a plain { locations, flows } object as `data`
      // (isFlowmapData) and builds a LocalFlowmapDataProvider internally from the
      // accessor props — the idiomatic flowmap.gl path (no hand-built provider).
      data: this._data,
      // Accessors map each record onto positions / ids / weights.
      getLocationId: (loc) => loc.id,
      getLocationLat: (loc) => loc.lat,
      getLocationLon: (loc) => loc.lon,
      getLocationName: (loc) => loc.id,
      getFlowOriginId: (f) => f.origin,
      getFlowDestId: (f) => f.dest,
      getFlowMagnitude: (f) => f.count,
      // Zoom-LOD super-nodes — the headline flowmap feature vs the hand-rolled v1.
      clusteringEnabled: this._clusteringEnabled,
      // Animated flow particles (9.4's enum; supersedes the deprecated
      // animationEnabled flag — 'straight' | 'animated-straight' | 'curved').
      flowLinesRenderingMode: 'animated-straight',
      // Let the LIBRARY own the width/colour scale domains (not a hand-rolled log).
      adaptiveScalesEnabled: true,
      // Default 1 = the existing scaled look.
      flowLineThicknessScale: 1,
      darkMode: true,
      colorScheme: 'Teal',
      // Node circles sized by their in/out totals.
      locationTotalsEnabled: true,
      // Top-N lever — flowmap draws only the strongest flows (tames the hairball).
      maxTopFlowsDisplayNum: this._topN,
      // Typed picking + hover -> floating tooltip (getTooltip) + panel readout.
      // FlowmapLayer resolves picking ASYNCHRONOUSLY and calls onHover(info, event)
      // where `info` is undefined on a hover-out (no object under the cursor), so we
      // must NOT destructure it blindly — guard for the undefined-info case.
      pickable: true,
      onHover: (info) => {
        const object = info && info.object ? info.object : null;
        self._hover = object;
        self._updatePickReadout(object);
      }
    });
  }

  _render() {
    if (!this.deck) return;
    this.deck.setProps({ layers: this._data ? [this._buildFlowmapLayer()] : [] });
  }

  // ---- Controls sync + mobile panel --------------------------------------

  _syncControlsToState() {
    if (this._sliceEls) {
      if (this._sliceEls.winSel) this._sliceEls.winSel.value = this._window;
      for (const r of (this._sliceEls.dayRadios || [])) r.checked = (r.value === this._daytype);
      if (this._sliceEls.hint) {
        const n = (this._rawFlows ? this._rawFlows.length : 0).toLocaleString();
        this._sliceEls.hint.innerHTML = `Slice <code>${this._sliceKey}</code> — ${n} corridors.`;
      }
    }
    if (this._scrubEls) {
      const h = this._activeHour;
      if (this._scrubEls.range) this._scrubEls.range.value = String(h);
      if (this._scrubEls.label) this._scrubEls.label.textContent = `${String(h).padStart(2, '0')}:00`;
    }
    if (this._topnEls) {
      if (this._topnEls.range) this._topnEls.range.value = String(this._topN);
      if (this._topnEls.label) this._topnEls.label.textContent = this._topN.toLocaleString();
    }
    if (this._clusterEls && this._clusterEls.box) this._clusterEls.box.checked = this._clusteringEnabled;
    if (this._basemapEls && this._basemapEls.sel) this._basemapEls.sel.value = this._basemapKey;
  }

  _buildPanelToggle() {
    const btn = document.getElementById('panel-toggle');
    const app = document.getElementById('app');
    if (!btn || !app) return;
    app.dataset.panel = 'open';
    btn.addEventListener('click', () => {
      const open = app.dataset.panel !== 'closed';
      app.dataset.panel = open ? 'closed' : 'open';
      btn.setAttribute('aria-expanded', open ? 'false' : 'true');
    });
  }
}

// Boot.
const app = new FlowmapApp();
app.start().catch((err) => {
  console.error('[flows_fm] start failed:', err && err.stack ? err.stack : err);
  window.__fmError = String(err && err.message ? err.message : err);
});
