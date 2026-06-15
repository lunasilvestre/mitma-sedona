/*
 * story-map.js — GENERIC sticky deck.gl + MapLibre renderer.
 *
 * A SINGLE deck.gl instance overlaid on a MapLibre map. It knows nothing about
 * Catalonia: everything theme/chapter-specific arrives via chapters.json (the
 * inline `window.STORY_CONFIG`) and the fetched story_data payloads.
 *
 * Listens (on document) for the scrolly.js seam:
 *   - "chapter-enter"   -> applies the chapter config (camera, layers, metric)
 *   - "map-reveal"      -> enters reveal (clip-path swipe) mode
 *   - "reveal-progress" -> drives the clip-path wipe 0..1
 *   - "map-compare"     -> enters compare (two side-by-side canvases) mode
 *
 * Basemap: KEYLESS Esri World Imagery raster (attribution required) + optional
 * CARTO dark-matter labels-only overlay.
 *
 * Depends on CDN globals: deck, maplibregl, h3 (h3-js).
 *
 * Public API:
 *   const sm = new StoryMap({ container, config });
 *   sm.init();
 */
(function (global) {
  'use strict';

  // ---- Colourblind-safe ramps (sampled viridis / magma / diverging RdBu) ----
  // 6 stops each, [r,g,b]. Sourced from matplotlib's perceptually-uniform maps.
  var RAMPS = {
    viridis: [
      [68, 1, 84], [59, 82, 139], [33, 144, 141],
      [93, 201, 99], [253, 231, 37], [253, 231, 37]
    ],
    magma: [
      [0, 0, 4], [60, 15, 111], [140, 41, 129],
      [222, 73, 104], [254, 159, 109], [252, 253, 191]
    ],
    plasma: [
      [13, 8, 135], [126, 3, 168], [203, 70, 121],
      [248, 149, 64], [253, 207, 58], [240, 249, 33]
    ],
    cividis: [
      [0, 32, 76], [40, 63, 97], [87, 91, 95],
      [134, 122, 92], [188, 156, 78], [255, 234, 70]
    ],
    // Diverging (sink<->source). Reversed RdBu so high=warm.
    RdBu: [
      [5, 48, 97], [67, 147, 195], [209, 229, 240],
      [253, 219, 199], [214, 96, 77], [103, 0, 31]
    ]
  };

  // NULL / no-data state: a distinct desaturated slate grey. NEVER the low end
  // of a ramp, NEVER 0 — honesty over smooth fields.
  var NULL_COLOR = [120, 128, 140];
  var NULL_ALPHA = 90;
  var FILL_ALPHA = 180; // ~0.7 so satellite reads through

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

  // OSM-domain colours for POIs (dark/satellite adapted, §8).
  var POI_COLORS = {
    medical: [176, 48, 82],     // maroon
    hospital: [176, 48, 82],
    pharmacy: [120, 200, 160],
    sport: [150, 110, 70],      // brown
    climb: [150, 110, 70],
    wellness: [120, 200, 210],  // teal
    yoga: [170, 130, 220],      // violet
    industry: [180, 140, 200],  // mauve
    _default: [136, 212, 255]   // accent
  };
  function poiColor(domain) {
    return POI_COLORS[domain] || POI_COLORS._default;
  }

  // ---------------------------------------------------------------------------
  function StoryMap(opts) {
    opts = opts || {};
    this.containerId = opts.container || 'map';
    this.config = opts.config || global.STORY_CONFIG || {};
    this.reducedMotion = global.matchMedia
      ? global.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false;

    this.map = null;        // MapLibre
    this.deck = null;       // deck.gl overlay (primary canvas)
    this.deckB = null;      // deck.gl for compare (right canvas), lazy
    this.mapB = null;       // MapLibre for compare, lazy

    this._chaptersById = {};
    this._data = {};        // { hexes, arcs, pois, distritos, manifest }
    this._fetching = {};    // in-flight fetch promises by key
    this._active = null;    // active chapter config
    this._mode = 'single';
    this._viewState = null;
  }

  StoryMap.prototype.init = function () {
    var self = this;
    var cfg = this.config;
    (cfg.chapters || []).forEach(function (c) { self._chaptersById[c.id] = c; });

    var iv = cfg.initial_view || { longitude: 0, latitude: 0, zoom: 2, pitch: 0, bearing: 0 };
    this._viewState = {
      longitude: iv.longitude, latitude: iv.latitude,
      zoom: iv.zoom, pitch: iv.pitch || 0, bearing: iv.bearing || 0
    };

    this._buildBasemap(this.containerId, this.map ? null : 'primary');
    this._buildDeck();
    this._bindEvents();
    return this;
  };

  // ---- Basemap: keyless Esri World Imagery raster (+ optional CARTO labels) --
  StoryMap.prototype._mapStyle = function () {
    var bm = this.config.basemap || {};
    var satUrl = bm.satellite ||
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
    var attribution = bm.attribution ||
      'Esri, Maxar, Earthstar Geographics, and the GIS User Community';

    var style = {
      version: 8,
      sources: {
        'esri-satellite': {
          type: 'raster',
          tiles: [satUrl],
          tileSize: 256,
          maxzoom: 19,
          attribution: attribution
        }
      },
      layers: [
        { id: 'esri-satellite', type: 'raster', source: 'esri-satellite' }
      ]
    };
    return style;
  };

  StoryMap.prototype._buildBasemap = function (containerId) {
    var self = this;
    var map = new maplibregl.Map({
      container: containerId,
      style: this._mapStyle(),
      center: [this._viewState.longitude, this._viewState.latitude],
      zoom: this._viewState.zoom,
      pitch: this._viewState.pitch,
      bearing: this._viewState.bearing,
      attributionControl: false,
      // deck.gl owns the camera (controller:true) and syncs us via
      // onViewStateChange; MapLibre stays passive so the two don't fight.
      interactive: false
    });
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');

    // Optional CARTO dark-matter labels-only overlay so place names read.
    var bm = this.config.basemap || {};
    if (bm.labels) {
      map.on('load', function () { self._addLabelsOverlay(map, bm.labels); });
    }
    this.map = map;
    return map;
  };

  StoryMap.prototype._addLabelsOverlay = function (map, styleUrl) {
    // Pull only symbol (label) layers from the CARTO dark-matter style so we
    // don't paint a second basemap over the satellite.
    fetch(styleUrl).then(function (r) { return r.json(); }).then(function (style) {
      var srcName = 'carto-labels';
      // find a vector source in the style to reuse for symbol layers
      var vectorSrc = null, vectorSrcName = null;
      Object.keys(style.sources || {}).forEach(function (k) {
        if (!vectorSrc && style.sources[k].type === 'vector') {
          vectorSrc = style.sources[k]; vectorSrcName = k;
        }
      });
      if (!vectorSrc) { return; }
      if (!map.getSource(srcName)) {
        map.addSource(srcName, vectorSrc);
      }
      (style.layers || []).forEach(function (lyr) {
        if (lyr.type === 'symbol') {
          var clone = JSON.parse(JSON.stringify(lyr));
          clone.id = 'lbl-' + clone.id;
          clone.source = srcName;
          try { map.addLayer(clone); } catch (e) { /* style mismatch — skip */ }
        }
      });
    }).catch(function () { /* labels are optional */ });
  };

  // ---- deck.gl overlay -------------------------------------------------------
  StoryMap.prototype._buildDeck = function () {
    var self = this;
    var canvas = document.createElement('canvas');
    canvas.id = this.containerId + '-deck';
    canvas.className = 'deck-canvas';
    var host = document.getElementById(this.containerId);
    host.appendChild(canvas);

    this.deck = new deck.Deck({
      canvas: canvas,
      width: '100%',
      height: '100%',
      initialViewState: this._viewState,
      controller: true,
      // keep MapLibre camera synced to deck's view state
      onViewStateChange: function (params) {
        self._viewState = params.viewState;
        self._syncBasemap(self.map, params.viewState);
      },
      getTooltip: function (info) { return self._tooltip(info); },
      layers: []
    });
  };

  StoryMap.prototype._syncBasemap = function (map, vs) {
    if (!map) { return; }
    map.jumpTo({
      center: [vs.longitude, vs.latitude],
      zoom: vs.zoom,
      bearing: vs.bearing,
      pitch: vs.pitch
    });
  };

  // ---- Event seam ------------------------------------------------------------
  StoryMap.prototype._bindEvents = function () {
    var self = this;
    document.addEventListener('chapter-enter', function (e) {
      self._onChapterEnter(e.detail.id);
    });
    document.addEventListener('map-reveal', function (e) {
      self._enterReveal(e.detail.id);
    });
    document.addEventListener('reveal-progress', function (e) {
      self._onRevealProgress(e.detail.id, e.detail.progress);
    });
    document.addEventListener('map-compare', function (e) {
      self._enterCompare(e.detail.id);
    });
  };

  StoryMap.prototype._onChapterEnter = function (id) {
    var ch = this._chaptersById[id];
    if (!ch) { return; }
    var self = this;
    this._active = ch;
    this._mode = ch.mode || 'single';

    // Reset reveal/compare chrome unless this chapter explicitly uses it.
    this._setMode(this._mode);

    // Lazy-fetch the data this chapter needs, then fly + rebuild.
    this._ensureData(ch.layers || []).then(function () {
      self._applyCamera(ch);
      self._rebuildLayers();
      self._renderLegend(ch);
    });
  };

  // ---- Camera ----------------------------------------------------------------
  StoryMap.prototype._applyCamera = function (ch) {
    var ms = ch.mapState || {};
    var target = {
      longitude: ms.longitude != null ? ms.longitude : this._viewState.longitude,
      latitude: ms.latitude != null ? ms.latitude : this._viewState.latitude,
      zoom: ms.zoom != null ? ms.zoom : this._viewState.zoom,
      pitch: ms.pitch != null ? ms.pitch : 0,
      bearing: ms.bearing != null ? ms.bearing : 0
    };

    if (this.reducedMotion) {
      // Instant swap — no eased camera move.
      this._viewState = target;
      this.deck.setProps({ initialViewState: target, viewState: undefined });
      this._syncBasemap(this.map, target);
      return;
    }

    var transitioned = Object.assign({}, target, {
      transitionDuration: 1600,
      transitionInterpolator: new deck.FlyToInterpolator({ speed: 1.4 }),
      transitionEasing: function (t) { return t * (2 - t); } // easeOutQuad
    });
    this._viewState = target;
    this.deck.setProps({ initialViewState: transitioned });
  };

  // ---- Data fetching (lazy) --------------------------------------------------
  StoryMap.prototype._dataUrl = function (key) {
    var d = this.config.data || {};
    return d[key];
  };

  StoryMap.prototype._ensureData = function (keys) {
    var self = this;
    var wanted = (keys || []).filter(function (k) {
      // distritos may piggyback on its own url; everything else maps 1:1
      return self._dataUrl(k) && !self._data[k] && !self._fetching[k];
    });
    var jobs = wanted.map(function (k) {
      var p = fetch(self._dataUrl(k))
        .then(function (r) {
          if (!r.ok) { throw new Error('fetch ' + k + ' ' + r.status); }
          return r.json();
        })
        .then(function (json) { self._data[k] = json; })
        .catch(function (err) {
          console.warn('[story-map] data fetch failed for', k, err);
          self._data[k] = [];
        });
      self._fetching[k] = p;
      return p;
    });
    // Also load the manifest once (for headline metrics / domains), if present.
    if (this._dataUrl('manifest') && !this._data.manifest && !this._fetching.manifest) {
      var mp = fetch(this._dataUrl('manifest'))
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (m) { self._data.manifest = m; })
        .catch(function () { self._data.manifest = {}; });
      this._fetching.manifest = mp;
      jobs.push(mp);
    }
    return Promise.all(jobs);
  };

  // ---- Metric domain (min/max) for ramp normalisation -----------------------
  StoryMap.prototype._metricDomain = function (metric) {
    // Prefer manifest-provided domain (computed at export time).
    var man = this._data.manifest || {};
    if (man.metrics && man.metrics[metric] &&
        man.metrics[metric].min != null && man.metrics[metric].max != null) {
      return [man.metrics[metric].min, man.metrics[metric].max];
    }
    // Fallback: scan hexes.
    var hexes = this._data.hexes || [];
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < hexes.length; i++) {
      var v = hexes[i][metric];
      if (v == null || isNaN(v)) { continue; }
      if (v < lo) { lo = v; }
      if (v > hi) { hi = v; }
    }
    if (lo === Infinity) { return [0, 1]; }
    if (lo === hi) { hi = lo + 1; }
    return [lo, hi];
  };

  // ---- Layer construction ----------------------------------------------------
  StoryMap.prototype._rebuildLayers = function () {
    var ch = this._active || {};
    var wanted = ch.layers || [];
    var layers = [];

    if (wanted.indexOf('distritos') !== -1) {
      var dl = this._distritoLayer();
      if (dl) { layers.push(dl); }
    }
    if (wanted.indexOf('hexes') !== -1 && ch.metric) {
      var hl = this._hexLayer(ch);
      if (hl) { layers.push(hl); }
    }
    if (wanted.indexOf('arcs') !== -1) {
      var al = this._arcLayer(ch);
      if (al) { layers.push(al); }
    }
    if (wanted.indexOf('pois') !== -1) {
      var pl = this._poiLayer(ch);
      if (pl) { layers.push(pl); }
    }
    this.deck.setProps({ layers: layers });
    if (this.deckB) { this.deckB.setProps({ layers: this._compareRightLayers(ch) }); }
  };

  StoryMap.prototype._hexLayer = function (ch) {
    var self = this;
    var data = this._data.hexes || [];
    if (!data.length) { return null; }
    var metric = ch.metric;
    var ramp = ch.colorRamp || 'viridis';
    var dom = this._metricDomain(metric);
    var lo = dom[0], span = (dom[1] - dom[0]) || 1;
    var extrude = !!ch.extrude;
    var extrudeScale = ch.extrudeScale != null ? ch.extrudeScale : 30;
    // elevationScale shrinks as zoom rises so towers don't occlude the base.
    var z = this._viewState.zoom || 8;
    var elevScale = extrude ? extrudeScale * Math.max(0.15, 12 / (z + 4)) : 0;

    return new deck.H3HexagonLayer({
      id: 'hexes-' + metric,
      data: data,
      pickable: true,
      wireframe: false,
      filled: true,
      extruded: extrude,
      elevationScale: elevScale,
      getHexagon: function (d) { return d.h3_id; },
      getFillColor: function (d) {
        var v = d[metric];
        if (v == null || isNaN(v)) {
          return [NULL_COLOR[0], NULL_COLOR[1], NULL_COLOR[2], NULL_ALPHA];
        }
        var t = clamp01((v - lo) / span);
        var c = rampColor(ramp, t);
        return [c[0], c[1], c[2], FILL_ALPHA];
      },
      getElevation: function (d) {
        if (!extrude) { return 0; }
        var v = d[metric];
        if (v == null || isNaN(v)) { return 0; }
        return clamp01((v - lo) / span);
      },
      updateTriggers: {
        getFillColor: [metric, ramp, lo, span],
        getElevation: [metric, extrude, lo, span]
      },
      transitions: this.reducedMotion ? {} : {
        getFillColor: 400,
        getElevation: 600
      }
    });
  };

  StoryMap.prototype._arcLayer = function () {
    var data = this._data.arcs || [];
    if (!data.length) { return null; }
    // width by log(flow); colour cyan -> magenta source->target.
    return new deck.ArcLayer({
      id: 'arcs',
      data: data,
      pickable: true,
      greatCircle: false,
      getSourcePosition: function (d) {
        return d.source || [d.source_lon, d.source_lat];
      },
      getTargetPosition: function (d) {
        return d.target || [d.target_lon, d.target_lat];
      },
      getSourceColor: [0, 220, 255, 200],   // cyan
      getTargetColor: [255, 0, 200, 200],    // magenta
      getWidth: function (d) {
        var f = d.flow || d.value || 1;
        return Math.max(1, Math.log10(f + 1) * 2);
      },
      widthUnits: 'pixels'
    });
  };

  StoryMap.prototype._poiLayer = function () {
    var data = this._data.pois || [];
    if (!data.length) { return null; }
    // Constant screen size, zoom-gated (hide < z11) per §8.
    var z = this._viewState.zoom || 8;
    if (z < 10.5) {
      return new deck.ScatterplotLayer({ id: 'pois', data: [] });
    }
    return new deck.ScatterplotLayer({
      id: 'pois',
      data: data,
      pickable: true,
      radiusUnits: 'pixels',
      getRadius: 5,
      radiusMinPixels: 3,
      radiusMaxPixels: 7,
      stroked: true,
      lineWidthMinPixels: 1,
      getLineColor: [10, 14, 26, 220],
      getPosition: function (d) {
        return d.position || [d.lon, d.lat];
      },
      getFillColor: function (d) {
        return poiColor(d.domain || d.osm_domain);
      }
    });
  };

  StoryMap.prototype._distritoLayer = function () {
    var data = this._data.distritos;
    if (!data) { return null; }
    // thin translucent dashed admin line (GeoJSON)
    return new deck.GeoJsonLayer({
      id: 'distritos',
      data: data,
      stroked: true,
      filled: false,
      getLineColor: [255, 255, 255, 60],
      getLineWidth: 1,
      lineWidthUnits: 'pixels',
      lineWidthMinPixels: 1
    });
  };

  // ---- Tooltip ---------------------------------------------------------------
  StoryMap.prototype._tooltip = function (info) {
    if (!info || !info.object) { return null; }
    var ch = this._active || {};
    var o = info.object;

    // Hex tooltip: active metric value + null-state label.
    if (info.layer && info.layer.id && info.layer.id.indexOf('hexes') === 0) {
      var metric = ch.metric;
      var label = ch.metricLabel || metric;
      var v = o[metric];
      var valueHtml;
      if (v == null || isNaN(v)) {
        valueHtml = '<span class="tt-null">' +
          (ch.nullState || 'no data') + '</span>';
      } else {
        valueHtml = '<strong>' + formatNum(v) + '</strong>';
      }
      return {
        html: '<div class="story-tooltip">' +
          '<div class="tt-label">' + esc(label) + '</div>' +
          '<div class="tt-value">' + valueHtml + '</div>' +
          '<div class="tt-id">' + esc(o.h3_id || '') + '</div>' +
          '</div>',
        style: { background: 'transparent', boxShadow: 'none' }
      };
    }
    // POI tooltip
    if (info.layer && info.layer.id === 'pois') {
      return {
        html: '<div class="story-tooltip"><div class="tt-label">' +
          esc(o.name || o.domain || 'POI') + '</div>' +
          '<div class="tt-id">' + esc(o.domain || '') + '</div></div>',
        style: { background: 'transparent', boxShadow: 'none' }
      };
    }
    // Arc tooltip
    if (info.layer && info.layer.id === 'arcs') {
      var f = o.flow || o.value;
      return {
        html: '<div class="story-tooltip"><div class="tt-label">flow</div>' +
          '<div class="tt-value"><strong>' + formatNum(f) + '</strong></div></div>',
        style: { background: 'transparent', boxShadow: 'none' }
      };
    }
    return null;
  };

  // ---- Mode switching: single / reveal / compare ----------------------------
  StoryMap.prototype._setMode = function (mode) {
    var host = document.getElementById(this.containerId);
    if (!host) { return; }
    host.classList.remove('mode-single', 'mode-reveal', 'mode-compare');
    host.classList.add('mode-' + (mode || 'single'));
    if (mode !== 'compare') { this._teardownCompare(); }
    if (mode !== 'reveal') { this._resetClip(); }
  };

  // reveal: clip-path swipe on the deck canvas between two states. We render the
  // "after" state on the primary deck and reveal it left->right over the "before"
  // basemap as the user scrolls (reveal-progress 0..1).
  StoryMap.prototype._enterReveal = function (id) {
    var ch = this._chaptersById[id];
    if (!ch) { return; }
    this._resetClip();
    // primary deck already shows the chapter's "after" layers via rebuild.
  };

  StoryMap.prototype._onRevealProgress = function (id, progress) {
    if (this._mode !== 'reveal') { return; }
    var canvas = document.getElementById(this.containerId + '-deck');
    if (!canvas) { return; }
    var pct = Math.round(clamp01(progress) * 100);
    // Wipe the thematic layer in from the left as the panel scrolls through.
    canvas.style.clipPath = 'inset(0 ' + (100 - pct) + '% 0 0)';
    var handle = document.getElementById(this.containerId + '-reveal-handle');
    if (handle) { handle.style.left = pct + '%'; }
  };

  StoryMap.prototype._resetClip = function () {
    var canvas = document.getElementById(this.containerId + '-deck');
    if (canvas) { canvas.style.clipPath = ''; }
  };

  // compare: two side-by-side deck canvases. The right one is lazily created and
  // shows the SAME chapter metric with the chapter's `compareRamp`/`compareMetric`
  // (or a diverging variant) so the reader contrasts two states.
  StoryMap.prototype._enterCompare = function (id) {
    var ch = this._chaptersById[id];
    if (!ch) { return; }
    this._buildCompare();
  };

  StoryMap.prototype._buildCompare = function () {
    if (this.deckB) { return; }
    var self = this;
    var host = document.getElementById(this.containerId);

    // Right basemap
    var mapBHost = document.createElement('div');
    mapBHost.id = this.containerId + '-mapB';
    mapBHost.className = 'compare-pane compare-right';
    host.appendChild(mapBHost);

    this.mapB = new maplibregl.Map({
      container: mapBHost.id,
      style: this._mapStyle(),
      center: [this._viewState.longitude, this._viewState.latitude],
      zoom: this._viewState.zoom,
      pitch: this._viewState.pitch,
      bearing: this._viewState.bearing,
      attributionControl: false,
      interactive: false
    });

    var canvasB = document.createElement('canvas');
    canvasB.id = this.containerId + '-deckB';
    canvasB.className = 'deck-canvas compare-right';
    mapBHost.appendChild(canvasB);

    this.deckB = new deck.Deck({
      canvas: canvasB,
      width: '100%',
      height: '100%',
      viewState: this._viewState,
      controller: false,
      layers: this._compareRightLayers(this._active)
    });

    // Keep the right pane camera locked to the primary.
    var sync = function () {
      if (!self.deckB) { return; }
      self.deckB.setProps({ viewState: self._viewState });
      self._syncBasemap(self.mapB, self._viewState);
    };
    this._compareSync = setInterval(sync, 100);
  };

  StoryMap.prototype._compareRightLayers = function (ch) {
    if (!ch) { return []; }
    // Right pane: the alternate metric/ramp for contrast. Falls back to the same
    // hex layer with a magma ramp if no explicit compare config is given.
    var alt = Object.assign({}, ch, {
      metric: ch.compareMetric || ch.metric,
      colorRamp: ch.compareRamp || 'magma'
    });
    var l = this._hexLayer(alt);
    return l ? [l] : [];
  };

  StoryMap.prototype._teardownCompare = function () {
    if (this._compareSync) { clearInterval(this._compareSync); this._compareSync = null; }
    if (this.deckB) { this.deckB.finalize(); this.deckB = null; }
    if (this.mapB) { this.mapB.remove(); this.mapB = null; }
    var hostB = document.getElementById(this.containerId + '-mapB');
    if (hostB && hostB.parentNode) { hostB.parentNode.removeChild(hostB); }
  };

  // ---- Legend ----------------------------------------------------------------
  StoryMap.prototype._renderLegend = function (ch) {
    var el = document.getElementById('story-legend');
    if (!el) { return; }
    if (!ch.metric) {
      el.innerHTML = '';
      el.classList.remove('visible');
      return;
    }
    var dom = this._metricDomain(ch.metric);
    var ramp = ch.colorRamp || 'viridis';
    var stops = 6, swatches = '';
    for (var i = 0; i < stops; i++) {
      var c = rampColor(ramp, i / (stops - 1));
      swatches += '<span class="lg-swatch" style="background:rgb(' +
        c[0] + ',' + c[1] + ',' + c[2] + ')"></span>';
    }
    el.innerHTML =
      '<div class="lg-title">' + esc(ch.metricLabel || ch.metric) + '</div>' +
      '<div class="lg-ramp">' + swatches + '</div>' +
      '<div class="lg-scale"><span>' + formatNum(dom[0]) + '</span>' +
      '<span>' + formatNum(dom[1]) + '</span></div>' +
      '<div class="lg-null"><span class="lg-swatch lg-null-swatch"></span>' +
      esc(ch.nullState || 'no data') + '</div>';
    el.classList.add('visible');
  };

  // ---- helpers ---------------------------------------------------------------
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function formatNum(v) {
    if (v == null || isNaN(v)) { return '—'; }
    var n = Number(v);
    if (Math.abs(n) >= 1000) { return n.toLocaleString(); }
    if (Number.isInteger(n)) { return String(n); }
    return n.toFixed(2);
  }

  StoryMap.RAMPS = RAMPS;
  StoryMap.rampColor = rampColor;
  StoryMap.NULL_COLOR = NULL_COLOR;
  global.StoryMap = StoryMap;
})(window);
