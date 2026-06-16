/*
 * geobrowser-map.js — self-contained deck.gl + MapLibre geo-browser for the
 * mitma-sedona Catalonia liveability index.
 *
 * A SINGLE deck.gl instance overlaid on a keyless Esri World Imagery satellite
 * basemap (MapLibre raster). Unlike the retired story-map.js, this module has NO
 * scrollytelling seam: the UI in explore.html drives it directly through a small
 * public API (setPreset, setMetric, setExtrude, toggleLayer, …). The symbology
 * (colourblind ramps, slate-grey nulls, OSM-domain POI colours, OD arcs) is
 * carried over verbatim from the prototype's viz logic.
 *
 * Depends on CDN globals: deck, maplibregl, h3 (h3-js).
 *
 * Public API:
 *   const gb = new GeoBrowser({ container, dataBase });
 *   gb.init().then(() => { ... });          // loads hexes + manifest, renders
 *   gb.setPreset('nature_first');           // recolour from score_<preset>
 *   gb.setMetric('mitma_inflow_daily');     // recolour by analytic dimension
 *   gb.setExtrude(true);                    // 2.5D extrusion on the active field
 *   gb.toggleLayer('arcs', true);           // input layers, lazy-loaded
 *   gb.onLegend = (info) => { ... };        // legend-render callback
 */
(function (global) {
  'use strict';

  // ---- Colourblind-safe ramps (sampled viridis / magma / diverging RdBu) ----
  // 6 stops each, [r,g,b], from matplotlib's perceptually-uniform maps.
  var RAMPS = {
    viridis: [
      [68, 1, 84], [59, 82, 139], [33, 144, 141],
      [93, 201, 99], [253, 231, 37], [253, 231, 37]
    ],
    magma: [
      [0, 0, 4], [60, 15, 111], [140, 41, 129],
      [222, 73, 104], [254, 159, 109], [252, 253, 191]
    ],
    // Diverging (sink<->source). Reversed RdBu so high=warm.
    RdBu: [
      [5, 48, 97], [67, 147, 195], [209, 229, 240],
      [253, 219, 199], [214, 96, 77], [103, 0, 31]
    ]
  };

  // NULL / no-data: a distinct desaturated slate grey. NEVER the low end of a
  // ramp, NEVER 0 — honesty over smooth fields.
  var NULL_COLOR = [120, 128, 140];
  var NULL_ALPHA = 110;
  var FILL_ALPHA = 185; // ~0.72 so satellite reads through (default; live-tunable)

  // ---- Basemap registry (all keyless / no API token) -----------------------
  // Each entry resolves to a MapLibre `style` (a GL-JSON URL string for the
  // vector styles, or an inline raster style object) plus an attribution string
  // for the credit control. The deck.gl overlay is a SEPARATE sibling canvas
  // (CSS z-index above the MapLibre canvas), so swapping the MapLibre style via
  // setStyle() never disturbs it — the hexes stay on top by construction.
  var ESRI_ATTRIB =
    'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community';
  function esriRasterStyle() {
    return {
      version: 8,
      sources: {
        'esri-satellite': {
          type: 'raster',
          tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
          tileSize: 256, maxzoom: 19, attribution: ESRI_ATTRIB
        }
      },
      layers: [{ id: 'esri-satellite', type: 'raster', source: 'esri-satellite' }]
    };
  }
  // Compliant fallback for the OSM basemap. We deliberately do NOT hotlink the
  // public OSM raster tile servers: OSM's tile-usage policy forbids it and
  // returns 403 "Access blocked / Referer required" for cross-origin requests.
  // CARTO Voyager is a keyless, policy-compliant OSM-standard coloured GL style.
  var CARTO_VOYAGER = 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json';
  var CARTO_ATTRIB = '&copy; OpenStreetMap contributors &copy; CARTO';
  var BASEMAPS = {
    satellite: { label: 'Satellite', style: esriRasterStyle, attribution: ESRI_ATTRIB },
    dark: {
      label: 'Dark', style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
      attribution: CARTO_ATTRIB
    },
    light: {
      label: 'Light', style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
      attribution: CARTO_ATTRIB
    },
    osm: {
      label: 'OSM', style: 'https://tiles.openfreemap.org/styles/bright',
      // OpenFreeMap Bright (vector); falls back to CARTO Voyager (keyless,
      // OSM-standard, policy-compliant) if OpenFreeMap is unreachable. Never
      // raw public OSM raster tiles (those 403 on hotlink).
      attribution: '&copy; OpenStreetMap contributors &copy; OpenFreeMap',
      fallbackStyle: CARTO_VOYAGER, fallbackAttribution: CARTO_ATTRIB
    }
  };

  // OSM-domain colours for POIs (dark/satellite adapted, spec §8).
  var POI_COLORS = {
    climbing: [150, 110, 70],   // brown (sport)
    yoga: [170, 130, 220],      // violet (wellness)
    hospital: [176, 48, 82]     // maroon (medical)
  };

  function clamp01(t) { return t < 0 ? 0 : t > 1 ? 1 : t; }

  // Sample a 6-stop ramp at t in [0,1] -> [r,g,b].
  function rampColor(name, t) {
    var stops = RAMPS[name] || RAMPS.viridis;
    t = clamp01(t);
    var seg = (stops.length - 1) * t;
    var i = Math.floor(seg);
    var f = seg - i;
    var a = stops[i];
    var b = stops[Math.min(i + 1, stops.length - 1)];
    return [
      Math.round(a[0] + (b[0] - a[0]) * f),
      Math.round(a[1] + (b[1] - a[1]) * f),
      Math.round(a[2] + (b[2] - a[2]) * f)
    ];
  }

  // ---------------------------------------------------------------------------
  // Field registry: which column each "view" colours by, its ramp, label, and
  // ramp semantics. `kind: 'presence'` fields are sparse (e.g. minutes to the
  // nearest station): low value = good/near, null = "none within reach" (grey).
  // `kind: 'diverging'` centres the ramp on a pivot (through-ratio 1.0).
  var FIELDS = {
    // OUTPUT — liveability score presets (resolved at runtime to score_<preset>)
    score: {
      column: 'score_default', ramp: 'viridis', label: 'Liveability score',
      kind: 'score', unit: '', lowLabel: 'low', highLabel: 'high'
    },
    // ANALYTIC dimensions
    mitma_inflow_daily: {
      column: 'mitma_inflow_daily', ramp: 'magma', label: 'MITMA inflow (trips/day)',
      kind: 'sequential', unit: ' trips/day', lowLabel: 'few', highLabel: 'many'
    },
    mitma_through_ratio: {
      column: 'mitma_through_ratio', ramp: 'RdBu', label: 'Through-ratio (sink ↔ source)',
      kind: 'diverging', pivot: 1.0, unit: '', lowLabel: 'sink', highLabel: 'source'
    },
    train_reach_min: {
      column: 'train_reach_min', ramp: 'viridis', label: 'Train reach (min, bike)',
      kind: 'presence', invert: true, unit: ' min', lowLabel: 'near', highLabel: 'far'
    },
    trains_to_bcn_nearest: {
      column: 'trains_to_bcn_nearest', ramp: 'magma', label: 'Trains/day to BCN core',
      kind: 'sequential', unit: ' trips/day', lowLabel: 'none', highLabel: 'frequent'
    },
    climb_min_m: {
      column: 'climb_min_m', ramp: 'viridis', label: 'Climbing gym (min away)',
      kind: 'presence', invert: true, unit: ' min', lowLabel: 'near', highLabel: 'far'
    },
    tree_cover_pct: {
      column: 'tree_cover_pct', ramp: 'viridis', label: 'Tree-cover density (%)',
      kind: 'sequential', unit: ' %', lowLabel: 'bare', highLabel: 'forested'
    },
    natura2000_within_5km: {
      column: 'natura2000_within_5km', ramp: 'viridis', label: 'Natura 2000 within 5 km',
      kind: 'boolean', unit: '', lowLabel: 'no', highLabel: 'yes'
    },
    biodiversity_obs_density: {
      column: 'biodiversity_obs_density', ramp: 'viridis', label: 'Biodiversity obs density (/km²)',
      kind: 'sequential', unit: ' /km²', lowLabel: 'few', highLabel: 'many'
    },
    no2_ugm3: {
      column: 'no2_ugm3', ramp: 'magma', label: 'NO₂ annual mean (µg/m³)',
      kind: 'sequential', unit: ' µg/m³', lowLabel: 'clean', highLabel: 'polluted'
    },
    pm25_ugm3: {
      column: 'pm25_ugm3', ramp: 'magma', label: 'PM₂.₅ annual mean (µg/m³)',
      kind: 'sequential', unit: ' µg/m³', lowLabel: 'clean', highLabel: 'polluted'
    },
    uhi_delta_c: {
      column: 'uhi_delta_c', ramp: 'magma', label: 'Urban-heat-island Δ (°C)',
      kind: 'diverging', pivot: 0.0, unit: ' °C', lowLabel: 'cooler', highLabel: 'hotter'
    },
    viirs_radiance: {
      column: 'viirs_radiance', ramp: 'magma', label: 'Night-light radiance (VIIRS)',
      kind: 'sequential', unit: '', lowLabel: 'dark', highLabel: 'bright'
    },
    eprtr_facility_min_m: {
      column: 'eprtr_facility_min_m', ramp: 'magma', label: 'E-PRTR facility (m away)',
      kind: 'presence', invert: true, unit: ' m', lowLabel: 'near', highLabel: 'far'
    },
    industry_density_per_km2: {
      column: 'industry_density_per_km2', ramp: 'magma', label: 'Industry density (/km²)',
      kind: 'sequential', unit: ' /km²', lowLabel: 'none', highLabel: 'dense'
    },
    motorway_within_500m: {
      column: 'motorway_within_500m', ramp: 'magma', label: 'Motorway within 500 m',
      kind: 'boolean', unit: '', lowLabel: 'no', highLabel: 'yes'
    }
  };

  function GeoBrowser(opts) {
    opts = opts || {};
    this.containerId = opts.container || 'map';
    this.dataBase = opts.dataBase || 'story_data/';
    this.initialView = opts.initialView || {
      longitude: 1.7, latitude: 41.6, zoom: 7.6, pitch: 0, bearing: 0
    };
    this.reducedMotion = global.matchMedia
      ? global.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false;

    this.map = null;     // MapLibre basemap
    this.deck = null;    // deck.gl overlay
    this._hexes = [];
    this._arcs = null;
    this._pois = null;
    this.manifest = {};

    // View state of the thematic hex layer.
    this._fieldKey = 'score';   // active field key into FIELDS
    this._preset = 'default';   // active score preset
    this._extrude = false;
    this._layersOn = { hexes: true, arcs: false, pois: false };
    this._basemapKey = opts.basemap && BASEMAPS[opts.basemap] ? opts.basemap : 'satellite';
    this._fillAlpha = opts.fillAlpha != null ? opts.fillAlpha : FILL_ALPHA; // live-tunable hex opacity

    this._viewState = {
      longitude: this.initialView.longitude,
      latitude: this.initialView.latitude,
      zoom: this.initialView.zoom,
      pitch: this.initialView.pitch || 0,
      bearing: this.initialView.bearing || 0
    };

    this.onLegend = null;   // callback(legendInfo)
    this._domainCache = {};
  }

  // ---- init: build basemap + deck, load hexes + manifest, first render ------
  GeoBrowser.prototype.init = function () {
    var self = this;
    this._buildBasemap();
    this._buildDeck();
    return Promise.all([
      this._fetchJson(this.dataBase + 'hexes.json').then(function (d) { self._hexes = d || []; }),
      this._fetchJson(this.dataBase + 'manifest.json').then(function (m) { self.manifest = m || {}; })
    ]).then(function () {
      self._render();
      return self;
    });
  };

  GeoBrowser.prototype._fetchJson = function (url) {
    return fetch(url).then(function (r) {
      if (!r.ok) { throw new Error('fetch ' + url + ' -> ' + r.status); }
      return r.json();
    });
  };

  // ---- Basemap: keyless MapLibre style from the BASEMAPS registry -----------
  GeoBrowser.prototype._resolveStyle = function (key) {
    var bm = BASEMAPS[key] || BASEMAPS.satellite;
    return typeof bm.style === 'function' ? bm.style() : bm.style;
  };

  GeoBrowser.prototype._buildBasemap = function () {
    var self = this;
    var bm = BASEMAPS[this._basemapKey] || BASEMAPS.satellite;
    var map = new maplibregl.Map({
      container: this.containerId,
      style: this._resolveStyle(this._basemapKey),
      center: [this._viewState.longitude, this._viewState.latitude],
      zoom: this._viewState.zoom,
      pitch: this._viewState.pitch,
      bearing: this._viewState.bearing,
      attributionControl: false,
      // deck.gl owns the camera (controller:true) and syncs MapLibre via
      // onViewStateChange; MapLibre stays passive so the two don't fight.
      interactive: false
    });
    this._attribCtrl = new maplibregl.AttributionControl({
      compact: true, customAttribution: bm.attribution
    });
    map.addControl(this._attribCtrl, 'bottom-right');
    // OpenFreeMap (vector) can be unreachable; fall back to CARTO Voyager.
    map.on('error', function (e) {
      self._onBasemapError(e);
    });
    this.map = map;
  };

  // If the active basemap is OSM (OpenFreeMap vector) and its style/tiles fail,
  // swap to the CARTO Voyager fallback once (keyless, OSM-standard, policy
  // compliant — never raw public OSM raster tiles). Other styles surface errors
  // but are not auto-swapped (they degrade to a partial basemap, not a crash).
  GeoBrowser.prototype._onBasemapError = function () {
    var bm = BASEMAPS[this._basemapKey];
    if (!bm || !bm.fallbackStyle || this._basemapFellBack) { return; }
    this._basemapFellBack = true;
    // fallbackStyle is a GL-JSON URL string (resolved by setStyle directly).
    this._applyStyle(bm.fallbackStyle, bm.fallbackAttribution);
  };

  // Swap the MapLibre style in place, preserving the camera. The deck overlay is
  // a separate sibling canvas (z-index above), so it is untouched and the hexes
  // stay on top; we only re-sync the camera + attribution after the style loads.
  GeoBrowser.prototype._applyStyle = function (style, attribution) {
    var self = this;
    var vs = this._viewState;
    this.map.setStyle(style, { diff: false });
    this.map.once('styledata', function () {
      self.map.jumpTo({
        center: [vs.longitude, vs.latitude],
        zoom: vs.zoom, bearing: vs.bearing, pitch: vs.pitch
      });
    });
    if (this._attribCtrl && attribution != null) {
      // Re-mount the attribution control with the new credit (MapLibre has no
      // public setter for customAttribution).
      try { this.map.removeControl(this._attribCtrl); } catch (e) { /* ignore */ }
      this._attribCtrl = new maplibregl.AttributionControl({
        compact: true, customAttribution: attribution
      });
      this.map.addControl(this._attribCtrl, 'bottom-right');
    }
  };

  // ---- deck.gl overlay -------------------------------------------------------
  GeoBrowser.prototype._buildDeck = function () {
    var self = this;
    var canvas = document.createElement('canvas');
    canvas.id = this.containerId + '-deck';
    canvas.className = 'deck-canvas';
    document.getElementById(this.containerId).appendChild(canvas);

    this.deck = new deck.Deck({
      canvas: canvas,
      width: '100%',
      height: '100%',
      initialViewState: this._viewState,
      controller: true,
      onViewStateChange: function (params) {
        self._viewState = params.viewState;
        if (self.map) {
          self.map.jumpTo({
            center: [params.viewState.longitude, params.viewState.latitude],
            zoom: params.viewState.zoom,
            bearing: params.viewState.bearing,
            pitch: params.viewState.pitch
          });
        }
        // POIs are zoom-gated; re-render when crossing the threshold.
        if (self._layersOn.pois) { self._scheduleRender(); }
      },
      getTooltip: function (info) { return self._tooltip(info); },
      layers: []
    });
  };

  GeoBrowser.prototype._scheduleRender = function () {
    var self = this;
    if (this._renderQueued) { return; }
    this._renderQueued = true;
    global.requestAnimationFrame(function () {
      self._renderQueued = false;
      self._render();
    });
  };

  // ---- Active field resolution ----------------------------------------------
  GeoBrowser.prototype._activeField = function () {
    var f = FIELDS[this._fieldKey];
    if (this._fieldKey === 'score') {
      // Resolve the score preset to its actual column.
      return Object.assign({}, f, { column: 'score_' + this._preset });
    }
    return f;
  };

  // Domain (min/max) for a column. Prefer manifest stats; else scan hexes once.
  GeoBrowser.prototype._domain = function (column) {
    if (this._domainCache[column]) { return this._domainCache[column]; }
    var dom = null;

    // Score columns: manifest.score_stats[<preset>].{min,max}
    if (column.indexOf('score_') === 0) {
      var preset = column.slice('score_'.length);
      var ss = (this.manifest.score_stats || {})[preset];
      if (ss && ss.min != null && ss.max != null) { dom = [ss.min, ss.max]; }
    }
    if (!dom) {
      var lo = Infinity, hi = -Infinity;
      var hx = this._hexes;
      for (var i = 0; i < hx.length; i++) {
        var v = hx[i][column];
        if (v == null || v === false || v === true) {
          if (v === true) { hi = Math.max(hi, 1); }
          if (v === false) { lo = Math.min(lo, 0); }
          continue;
        }
        if (typeof v !== 'number' || isNaN(v)) { continue; }
        if (v < lo) { lo = v; }
        if (v > hi) { hi = v; }
      }
      if (lo === Infinity) { dom = [0, 1]; }
      else { if (lo === hi) { hi = lo + 1; } dom = [lo, hi]; }
    }
    this._domainCache[column] = dom;
    return dom;
  };

  // Normalise a raw value to [0,1] given the field's ramp semantics.
  GeoBrowser.prototype._normalise = function (field, v, dom) {
    if (field.kind === 'boolean') { return v ? 1 : 0; }
    if (field.kind === 'diverging') {
      // Map pivot -> 0.5; spread by the larger half-range so the ramp is honest.
      var pivot = field.pivot != null ? field.pivot : 0;
      var half = Math.max(pivot - dom[0], dom[1] - pivot) || 1;
      return clamp01(0.5 + (v - pivot) / (2 * half));
    }
    var lo = dom[0], span = (dom[1] - dom[0]) || 1;
    var t = clamp01((v - lo) / span);
    return field.invert ? 1 - t : t; // presence fields: near=good=bright
  };

  // ---- Render all active layers ---------------------------------------------
  GeoBrowser.prototype._render = function () {
    var layers = [];
    if (this._layersOn.hexes) {
      var hl = this._hexLayer();
      if (hl) { layers.push(hl); }
    }
    if (this._layersOn.arcs && this._arcs) {
      var al = this._arcLayer();
      if (al) { layers.push(al); }
    }
    if (this._layersOn.pois && this._pois) {
      var pls = this._poiLayers();
      for (var i = 0; i < pls.length; i++) { layers.push(pls[i]); }
    }
    this.deck.setProps({ layers: layers });
    this._emitLegend();
  };

  GeoBrowser.prototype._hexLayer = function () {
    var self = this;
    var data = this._hexes;
    if (!data.length) { return null; }
    var field = this._activeField();
    var column = field.column;
    var ramp = field.ramp;
    var dom = this._domain(column);
    var extrude = this._extrude;
    // elevationScale shrinks as zoom rises so towers don't occlude the base.
    var z = this._viewState.zoom || 8;
    var elevScale = extrude ? 22000 * Math.max(0.15, 9 / (z + 4)) : 0;

    return new deck.H3HexagonLayer({
      id: 'hexes',
      data: data,
      pickable: true,
      wireframe: false,
      filled: true,
      stroked: false,
      extruded: extrude,
      elevationScale: elevScale,
      getHexagon: function (d) { return d.h3_id; },
      getFillColor: function (d) {
        var v = d[column];
        // Null cells scale with the slider too, but stay clearly dimmer than
        // data cells so "no data" never reads as a value.
        var nullA = Math.round(NULL_ALPHA * (self._fillAlpha / FILL_ALPHA));
        if (v == null || (typeof v === 'number' && isNaN(v))) {
          return [NULL_COLOR[0], NULL_COLOR[1], NULL_COLOR[2], nullA];
        }
        var t = self._normalise(field, v, dom);
        var c = rampColor(ramp, t);
        return [c[0], c[1], c[2], self._fillAlpha];
      },
      getElevation: function (d) {
        if (!extrude) { return 0; }
        var v = d[column];
        if (v == null || (typeof v === 'number' && isNaN(v))) { return 0; }
        return self._normalise(field, v, dom);
      },
      updateTriggers: {
        getFillColor: [column, ramp, dom[0], dom[1], field.kind, this._fillAlpha],
        getElevation: [column, extrude, dom[0], dom[1], field.kind]
      }
      // NOTE: deliberately NO per-attribute `transitions` here. Animated
      // getFillColor/getElevation interpolation across 45k instanced hexes
      // doubles the attribute buffers and overruns software-WebGL (swiftshader)
      // with "GL_INVALID_OPERATION: glDrawElementsInstanced: Insufficient
      // buffer size", which breaks headless rendering. Recolouring 45k static
      // hexes is instant without it; camera moves still ease via _flyTo.
    });
  };

  GeoBrowser.prototype._arcLayer = function () {
    var data = this._arcs || [];
    if (!data.length) { return null; }
    return new deck.ArcLayer({
      id: 'arcs',
      data: data,
      pickable: true,
      greatCircle: false,
      getSourcePosition: function (d) { return [d.source_lon, d.source_lat]; },
      getTargetPosition: function (d) { return [d.target_lon, d.target_lat]; },
      getSourceColor: [0, 220, 255, 190],   // cyan
      getTargetColor: [255, 0, 200, 190],    // magenta
      getWidth: function (d) {
        var f = d.flow || 1;
        return Math.max(1, Math.log10(f + 1) * 1.8);
      },
      widthUnits: 'pixels'
    });
  };

  // POIs: one ScatterplotLayer per category so each carries its domain colour
  // and a category tag for the tooltip. Zoom-gated (hidden < z10.5) per spec §8.
  GeoBrowser.prototype._poiLayers = function () {
    var pois = this._pois || {};
    var z = this._viewState.zoom || 8;
    if (z < 10.5) { return []; }
    var layers = [];
    Object.keys(pois).forEach(function (cat) {
      var rows = pois[cat] || [];
      if (!rows.length) { return; }
      var color = POI_COLORS[cat] || [136, 212, 255];
      layers.push(new deck.ScatterplotLayer({
        id: 'pois-' + cat,
        data: rows,
        pickable: true,
        radiusUnits: 'pixels',
        getRadius: 5,
        radiusMinPixels: 3,
        radiusMaxPixels: 8,
        stroked: true,
        lineWidthMinPixels: 1,
        getLineColor: [10, 14, 26, 220],
        getPosition: function (d) { return [d.lon, d.lat]; },
        getFillColor: color,
        // Stash the category on the layer for the tooltip.
        _poiCategory: cat
      }));
    });
    return layers;
  };

  // ---- Tooltip ---------------------------------------------------------------
  GeoBrowser.prototype._tooltip = function (info) {
    if (!info || !info.object) { return null; }
    var o = info.object;
    var id = info.layer && info.layer.id;

    if (id === 'hexes') {
      var field = this._activeField();
      var v = o[field.column];
      var valueHtml;
      if (v == null || (typeof v === 'number' && isNaN(v))) {
        valueHtml = '<span class="tt-null">no data</span>';
      } else if (field.kind === 'boolean') {
        valueHtml = '<strong>' + (v ? 'yes' : 'no') + '</strong>';
      } else {
        valueHtml = '<strong>' + formatNum(v) + '</strong>' + esc(field.unit || '');
      }
      return {
        html: '<div class="gb-tooltip">' +
          '<div class="tt-label">' + esc(field.label) + '</div>' +
          '<div class="tt-value">' + valueHtml + '</div>' +
          '<div class="tt-id">' + esc(o.h3_id || '') + '</div>' +
          '</div>',
        style: { background: 'transparent', boxShadow: 'none' }
      };
    }
    if (id && id.indexOf('pois-') === 0) {
      var cat = id.slice('pois-'.length);
      return {
        html: '<div class="gb-tooltip"><div class="tt-label">' + esc(cat) + '</div>' +
          '<div class="tt-value">' + esc(o.name || '(unnamed)') + '</div></div>',
        style: { background: 'transparent', boxShadow: 'none' }
      };
    }
    if (id === 'arcs') {
      return {
        html: '<div class="gb-tooltip"><div class="tt-label">MITMA OD flow</div>' +
          '<div class="tt-value"><strong>' + formatNum(o.flow) + '</strong> trips/day</div></div>',
        style: { background: 'transparent', boxShadow: 'none' }
      };
    }
    return null;
  };

  // ---- Legend emission -------------------------------------------------------
  GeoBrowser.prototype._emitLegend = function () {
    if (typeof this.onLegend !== 'function') { return; }
    var field = this._activeField();
    var dom = this._domain(field.column);
    var stops = [];
    var n = 6;
    for (var i = 0; i < n; i++) {
      stops.push(rampColor(field.ramp, i / (n - 1)));
    }
    var loVal, hiVal;
    if (field.kind === 'diverging') {
      loVal = formatNum(dom[0]); hiVal = formatNum(dom[1]);
    } else if (field.invert) {
      // presence: bright end = near (low minutes)
      loVal = formatNum(dom[1]) + esc(field.unit); hiVal = formatNum(dom[0]) + esc(field.unit);
    } else {
      loVal = formatNum(dom[0]) + esc(field.unit); hiVal = formatNum(dom[1]) + esc(field.unit);
    }
    this.onLegend({
      label: field.label,
      kind: field.kind,
      stops: stops,
      lowVal: loVal,
      highVal: hiVal,
      lowLabel: field.lowLabel,
      highLabel: field.highLabel,
      nullColor: NULL_COLOR
    });
  };

  // ---- Public API ------------------------------------------------------------
  GeoBrowser.prototype.setPreset = function (preset) {
    this._preset = preset;
    this._fieldKey = 'score';     // selecting a preset returns to the score view
    this._render();
  };

  GeoBrowser.prototype.setMetric = function (fieldKey) {
    if (!FIELDS[fieldKey]) { return; }
    this._fieldKey = fieldKey;
    this._render();
  };

  GeoBrowser.prototype.setExtrude = function (on) {
    this._extrude = !!on;
    // Give a little pitch when extruding so the towers read.
    if (on && this._viewState.pitch < 20) {
      this._flyTo({ pitch: 45 });
    } else if (!on && this._viewState.pitch > 5) {
      this._flyTo({ pitch: 0 });
    }
    this._render();
  };

  GeoBrowser.prototype._flyTo = function (partial) {
    var target = Object.assign({}, this._viewState, partial);
    this._viewState = target;
    if (this.reducedMotion) {
      this.deck.setProps({ initialViewState: target });
    } else {
      this.deck.setProps({
        initialViewState: Object.assign({}, target, {
          transitionDuration: 600,
          transitionInterpolator: new deck.LinearInterpolator(['pitch', 'bearing'])
        })
      });
    }
    if (this.map) {
      this.map.jumpTo({
        center: [target.longitude, target.latitude],
        zoom: target.zoom, bearing: target.bearing, pitch: target.pitch
      });
    }
  };

  // Toggle an input/output layer. Lazy-fetches arcs/pois on first enable.
  GeoBrowser.prototype.toggleLayer = function (key, on) {
    var self = this;
    this._layersOn[key] = !!on;
    if (on && key === 'arcs' && !this._arcs) {
      return this._fetchJson(this.dataBase + 'arcs.json')
        .then(function (d) { self._arcs = d || []; self._render(); })
        .catch(function (e) { self._layersOn.arcs = false; throw e; });
    }
    if (on && key === 'pois' && !this._pois) {
      return this._fetchJson(this.dataBase + 'pois.json')
        .then(function (d) { self._pois = d || {}; self._render(); })
        .catch(function (e) { self._layersOn.pois = false; throw e; });
    }
    this._render();
    return Promise.resolve();
  };

  GeoBrowser.prototype.resetView = function () {
    this._flyTo({
      longitude: this.initialView.longitude,
      latitude: this.initialView.latitude,
      zoom: this.initialView.zoom,
      pitch: this._extrude ? 45 : 0,
      bearing: 0
    });
  };

  // Swap the basemap (keeps camera + deck overlay). No-op if already active.
  GeoBrowser.prototype.setBasemap = function (key) {
    if (!BASEMAPS[key] || key === this._basemapKey) { return; }
    this._basemapKey = key;
    this._basemapFellBack = false;
    var bm = BASEMAPS[key];
    this._applyStyle(this._resolveStyle(key), bm.attribution);
  };

  // Live-adjust the hex fill opacity. `frac` is 0..1 (slider / 100). The deck
  // layer recolours in place via the updateTrigger on _fillAlpha — no re-fetch.
  GeoBrowser.prototype.setHexOpacity = function (frac) {
    var a = Math.round(clamp01(frac) * 255);
    this._fillAlpha = a;
    if (this._layersOn.hexes) { this._render(); }
  };

  // ---- helpers ---------------------------------------------------------------
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function formatNum(v) {
    if (v == null || (typeof v === 'number' && isNaN(v))) { return '—'; }
    var n = Number(v);
    if (Math.abs(n) >= 1000) { return n.toLocaleString(); }
    if (Number.isInteger(n)) { return String(n); }
    return n.toFixed(2);
  }

  GeoBrowser.RAMPS = RAMPS;
  GeoBrowser.FIELDS = FIELDS;
  GeoBrowser.BASEMAPS = BASEMAPS;
  GeoBrowser.DEFAULT_FILL_ALPHA = FILL_ALPHA;
  GeoBrowser.rampColor = rampColor;
  GeoBrowser.NULL_COLOR = NULL_COLOR;
  global.GeoBrowser = GeoBrowser;
})(window);
