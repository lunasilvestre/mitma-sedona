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

  // Categorical colour scales (kind:'categorical' fields). A fixed
  // label->[r,g,b] map so a class always reads the same colour across the map
  // and the legend. Colourblind-aware (Okabe-Ito-derived, distinct hues).
  var CATEGORICAL = {
    mobility_typology: {
      'commuter-corridor': [230, 159, 0],      // orange — high work/study pull
      'leisure-magnet': [204, 121, 167],       // pink — weekend/leisure draw
      'transit-corridor': [240, 228, 66],      // yellow — long through-trips
      'self-contained': [0, 158, 115],         // green — complete neighbourhood
      'mixed-balanced': [153, 153, 153]        // neutral grey — no dominant axis
    },
    peak_hour_bucket: {
      morning: [86, 180, 233],   // blue — am commute tide
      midday: [240, 228, 66],    // yellow — flat / midday
      evening: [230, 159, 0],    // orange — pm peak
      night: [120, 90, 200]      // violet — nightlife / overnight
    }
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

  // POIs are point-level OSM amenities, hidden below this zoom so they don't
  // smear into an unreadable dot-cloud at region scale. The number is shared by
  // the _poiLayers z-gate AND the inline "zoom in to reveal" hint (FIX 2).
  var POI_ZOOM_GATE = 10.5;

  // OSM-domain colours for POIs (dark/satellite adapted, spec §8).
  var POI_COLORS = {
    climbing: [150, 110, 70],   // brown (sport)
    yoga: [170, 130, 220],      // violet (wellness)
    hospital: [176, 48, 82]     // maroon (medical)
  };

  function clamp01(t) { return t < 0 ? 0 : t > 1 ? 1 : t; }

  // Stable fallback palette for categorical labels not in CATEGORICAL[column]
  // (e.g. a cluster suffix variant 'self-contained-2' the pipeline may emit).
  var CAT_FALLBACK = [
    [86, 180, 233], [230, 159, 0], [204, 121, 167], [240, 228, 66],
    [0, 158, 115], [153, 153, 153], [213, 94, 0], [0, 114, 178]
  ];
  // Resolve a categorical label to its colour: exact map hit, else the base
  // label with a trailing -<n> stripped, else a deterministic palette slot.
  function categoricalColor(column, label) {
    if (label == null) { return null; }
    var cmap = CATEGORICAL[column] || {};
    if (cmap[label]) { return cmap[label]; }
    var base = String(label).replace(/-\d+$/, '');
    if (cmap[base]) { return cmap[base]; }
    var h = 0;
    var s = String(label);
    for (var i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) & 0x7fffffff; }
    return CAT_FALLBACK[h % CAT_FALLBACK.length];
  }

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
      kind: 'score', goodWhen: 'high', unit: '', lowLabel: 'low', highLabel: 'high'
    },
    // ANALYTIC dimensions
    mitma_inflow_daily: {
      column: 'mitma_inflow_daily', ramp: 'magma', label: 'MITMA inflow (trips/day)',
      kind: 'sequential', goodWhen: 'neutral', unit: ' trips/day', lowLabel: 'few', highLabel: 'many'
    },
    mitma_outflow_daily: {
      column: 'mitma_outflow_daily', ramp: 'magma', label: 'MITMA outflow (trips/day)',
      kind: 'sequential', goodWhen: 'neutral', unit: ' trips/day', lowLabel: 'few', highLabel: 'many'
    },
    mitma_through_ratio: {
      column: 'mitma_through_ratio', ramp: 'RdBu', label: 'Through-ratio (sink ↔ source)',
      // v3 dasymetric: now log(outflow/inflow), so the natural pivot is 0.0.
      kind: 'diverging', goodWhen: 'neutral', pivot: 0.0, unit: '', lowLabel: 'sink', highLabel: 'source'
    },
    // === v3 MITMA deep-Spark mobility layers (Sedona dasymetric crosswalk) ===
    am_peak_share: {
      column: 'am_peak_share', ramp: 'magma', label: 'AM-peak trip share (07–09h)',
      kind: 'sequential', goodWhen: 'neutral', unit: '', lowLabel: 'flat', highLabel: 'tidal',
      seasonal: true, seasonKey: 'am'
    },
    pm_peak_share: {
      column: 'pm_peak_share', ramp: 'magma', label: 'PM-peak trip share (17–20h)',
      kind: 'sequential', goodWhen: 'neutral', unit: '', lowLabel: 'flat', highLabel: 'tidal',
      seasonal: true, seasonKey: 'pm'
    },
    midday_share: {
      column: 'midday_share', ramp: 'magma', label: 'Midday trip share (11–15h)',
      kind: 'sequential', goodWhen: 'neutral', unit: '', lowLabel: 'flat', highLabel: 'midday-led',
      seasonal: true, seasonKey: 'mid'
    },
    night_share: {
      column: 'night_share', ramp: 'magma', label: 'Night trip share (22–05h)',
      kind: 'sequential', goodWhen: 'neutral', unit: '', lowLabel: 'quiet', highLabel: 'active',
      seasonal: true, seasonKey: 'night'
    },
    peak_hour_bucket: {
      column: 'peak_hour_bucket', ramp: 'viridis', label: 'Peak-hour band',
      kind: 'categorical', goodWhen: 'neutral', unit: '',
      seasonal: true, seasonKey: 'peak'
    },
    weekend_weekday_ratio: {
      column: 'weekend_weekday_ratio', ramp: 'RdBu', label: 'Weekend ÷ weekday trips',
      kind: 'diverging', goodWhen: 'neutral', pivot: 1.0, unit: '×', lowLabel: 'weekday', highLabel: 'weekend',
      seasonal: true, seasonKey: 'wwr'
    },
    weekend_hotspot_score: {
      column: 'weekend_hotspot_score', ramp: 'RdBu', label: 'Weekend hotspot score',
      kind: 'diverging', goodWhen: 'neutral', pivot: 1.0, unit: '', lowLabel: 'weekday', highLabel: 'weekend draw',
      seasonal: true, seasonKey: 'whs'
    },
    // SEASONAL headline delta — its OWN field (independent of the Season dropdown).
    // Three calendar month-windows (Jun−Feb), NOT a climate average. Warm =
    // switches ON as a weekend hotspot in summer-onset; ~0 = no seasonal shift.
    weekend_hotspot_summer_minus_winter: {
      column: 'weekend_hotspot_summer_minus_winter', ramp: 'RdBu',
      label: 'Weekend pull: summer-onset − winter (Jun−Feb)',
      kind: 'diverging', goodWhen: 'neutral', pivot: 0.0, unit: '',
      lowLabel: 'more winter draw', highLabel: 'more summer draw'
    },
    // SECOND headline delta — the weekend÷weekday RATIO differenced the same way
    // (Jun−Feb), its own RdBu diverging field (pivot 0). Tighter range than the
    // hotspot delta (min −0.280 · median +0.005 · max +0.377). Inline in
    // hexes.json, so it needs no sidecar — independent of the Season dropdown.
    weekend_ratio_summer_minus_winter: {
      column: 'weekend_ratio_summer_minus_winter', ramp: 'RdBu',
      label: 'Weekend ratio: summer-onset − winter (Jun−Feb)',
      kind: 'diverging', goodWhen: 'neutral', pivot: 0.0, unit: '×',
      lowLabel: 'more winter draw', highLabel: 'more summer draw'
    },
    leisure_share: {
      column: 'leisure_share', ramp: 'viridis', label: 'Leisure-activity trip share',
      kind: 'sequential', goodWhen: 'neutral', unit: '', lowLabel: 'errand', highLabel: 'leisure',
      seasonal: true, seasonKey: 'leis'
    },
    commute_share: {
      column: 'commute_share', ramp: 'viridis', label: 'Commute-activity trip share',
      kind: 'sequential', goodWhen: 'neutral', unit: '', lowLabel: 'leisure', highLabel: 'commute',
      seasonal: true, seasonKey: 'comm'
    },
    mobility_typology: {
      column: 'mobility_typology', ramp: 'viridis', label: 'Mobility typology',
      kind: 'categorical', goodWhen: 'neutral', unit: ''
    },
    intra_zone_share: {
      column: 'intra_zone_share', ramp: 'viridis', label: 'Self-containment (intra-zone trips)',
      kind: 'sequential', goodWhen: 'high', unit: '', lowLabel: 'commuter', highLabel: 'self-contained'
    },
    geodemo_diversity: {
      column: 'geodemo_diversity', ramp: 'viridis', label: 'Geodemographic diversity (entropy)',
      kind: 'sequential', goodWhen: 'high', unit: ' bits', lowLabel: 'uniform', highLabel: 'mixed'
    },
    low_income_inflow_share: {
      column: 'low_income_inflow_share', ramp: 'viridis', label: 'Low-income inflow share',
      kind: 'sequential', goodWhen: 'neutral', unit: '', lowLabel: 'few', highLabel: 'many'
    },
    youth_mobility_share: {
      column: 'youth_mobility_share', ramp: 'viridis', label: 'Youth (0–25) inflow share',
      kind: 'sequential', goodWhen: 'neutral', unit: '', lowLabel: 'few', highLabel: 'many'
    },
    senior_mobility_share: {
      column: 'senior_mobility_share', ramp: 'viridis', label: 'Senior (65+) inflow share',
      kind: 'sequential', goodWhen: 'neutral', unit: '', lowLabel: 'few', highLabel: 'many'
    },
    female_share: {
      column: 'female_share', ramp: 'viridis', label: 'Female inflow share (of known-sex trips)',
      kind: 'sequential', goodWhen: 'neutral', unit: '', lowLabel: 'few', highLabel: 'many'
    },
    support_n: {
      column: 'support_n', ramp: 'magma', label: 'OD-segment row count (density proxy)',
      kind: 'sequential', goodWhen: 'neutral', unit: ' rows', lowLabel: 'sparse', highLabel: 'dense'
    },
    train_reach_min: {
      column: 'train_reach_min', ramp: 'viridis', label: 'Train reach (min, bike)',
      kind: 'presence', goodWhen: 'low', unit: ' min', lowLabel: 'near', highLabel: 'far'
    },
    trains_per_day_nearest: {
      column: 'trains_per_day_nearest', ramp: 'magma', label: 'Trains/day at nearest station',
      kind: 'sequential', goodWhen: 'high', unit: ' trips/day', lowLabel: 'none', highLabel: 'frequent'
    },
    trains_to_bcn_nearest: {
      column: 'trains_to_bcn_nearest', ramp: 'magma', label: 'Trains/day to BCN core',
      kind: 'sequential', goodWhen: 'high', unit: ' trips/day', lowLabel: 'none', highLabel: 'frequent'
    },
    climb_min_m: {
      column: 'climb_min_m', ramp: 'viridis', label: 'Climbing gym (min away)',
      kind: 'presence', goodWhen: 'low', unit: ' min', lowLabel: 'near', highLabel: 'far'
    },
    yoga_min_m: {
      column: 'yoga_min_m', ramp: 'viridis', label: 'Yoga studio (min away)',
      kind: 'presence', goodWhen: 'low', unit: ' min', lowLabel: 'near', highLabel: 'far'
    },
    hospital_min_m: {
      column: 'hospital_min_m', ramp: 'viridis', label: 'Hospital (min away)',
      kind: 'presence', goodWhen: 'low', unit: ' min', lowLabel: 'near', highLabel: 'far'
    },
    green_min_m: {
      column: 'green_min_m', ramp: 'viridis', label: 'Green space (min away)',
      kind: 'presence', goodWhen: 'low', unit: ' min', lowLabel: 'near', highLabel: 'far'
    },
    sea_min_m: {
      column: 'sea_min_m', ramp: 'viridis', label: 'Sea / coast (min away)',
      kind: 'presence', goodWhen: 'low', unit: ' min', lowLabel: 'near', highLabel: 'far'
    },
    pharmacy_density_per_km2: {
      column: 'pharmacy_density_per_km2', ramp: 'viridis', label: 'Pharmacy density (/km²)',
      kind: 'sequential', goodWhen: 'high', unit: ' /km²', lowLabel: 'none', highLabel: 'dense'
    },
    tree_cover_pct: {
      column: 'tree_cover_pct', ramp: 'viridis', label: 'Tree-cover density (%)',
      kind: 'sequential', goodWhen: 'high', unit: ' %', lowLabel: 'bare', highLabel: 'forested'
    },
    natura2000_within_5km: {
      column: 'natura2000_within_5km', ramp: 'viridis', label: 'Natura 2000 within 5 km',
      kind: 'boolean', goodWhen: 'high', unit: '', lowLabel: 'no', highLabel: 'yes'
    },
    biodiversity_obs_density: {
      column: 'biodiversity_obs_density', ramp: 'viridis', label: 'Biodiversity obs density (/km²)',
      kind: 'sequential', goodWhen: 'high', unit: ' /km²', lowLabel: 'few', highLabel: 'many'
    },
    no2_ugm3: {
      column: 'no2_ugm3', ramp: 'magma', label: 'NO₂ annual mean (µg/m³)',
      kind: 'sequential', goodWhen: 'low', unit: ' µg/m³', lowLabel: 'clean', highLabel: 'polluted'
    },
    pm25_ugm3: {
      column: 'pm25_ugm3', ramp: 'magma', label: 'PM₂.₅ annual mean (µg/m³)',
      kind: 'sequential', goodWhen: 'low', unit: ' µg/m³', lowLabel: 'clean', highLabel: 'polluted'
    },
    uhi_delta_c: {
      column: 'uhi_delta_c', ramp: 'magma', label: 'Urban-heat-island Δ (°C)',
      kind: 'diverging', goodWhen: 'low', pivot: 0.0, unit: ' °C', lowLabel: 'cooler', highLabel: 'hotter'
    },
    viirs_radiance: {
      column: 'viirs_radiance', ramp: 'magma', label: 'Night-light radiance (VIIRS)',
      kind: 'sequential', goodWhen: 'neutral', unit: '', lowLabel: 'dark', highLabel: 'bright'
    },
    eprtr_facility_min_m: {
      column: 'eprtr_facility_min_m', ramp: 'magma', label: 'E-PRTR facility (m away)',
      kind: 'presence', goodWhen: 'high', unit: ' m', lowLabel: 'near', highLabel: 'far'
    },
    industry_density_per_km2: {
      column: 'industry_density_per_km2', ramp: 'magma', label: 'Industry density (/km²)',
      kind: 'sequential', goodWhen: 'neutral', unit: ' /km²', lowLabel: 'none', highLabel: 'dense'
    },
    motorway_within_500m: {
      column: 'motorway_within_500m', ramp: 'magma', label: 'Motorway within 500 m',
      kind: 'boolean', goodWhen: 'neutral', unit: '', lowLabel: 'no', highLabel: 'yes'
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
    // OD-arc width domain over log10(flow+1), computed once when arcs.json loads.
    // arcs.json holds MILLION-scale flow values, so a raw log scale lands every
    // arc in a ~10.6–12.4px band (a uniform fat tangle); we instead map the
    // measured [logMin,logMax] linearly into ~1.5–8px for a thin->thick gradient.
    this._arcLogMin = null;
    this._arcLogMax = null;
    this._pois = null;
    this._rhythm = null;   // lazy-loaded h3_id -> [24 floats] (hover sparkline)
    this._seasons = null;  // lazy-loaded h3_id -> {feb:{...},may,jun} (season sidecar)
    this.manifest = {};

    // View state of the thematic hex layer.
    this._fieldKey = 'score';   // active field key into FIELDS
    this._preset = 'default';   // active score preset
    this._season = '';          // '' = Pooled (all 89 days); else 'feb'|'may'|'jun'
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

  // Lazy-load the 24h rhythm sibling (h3_id -> [24 floats]). Called once when a
  // rhythm field is first selected; the hover tooltip then draws a sparkline.
  GeoBrowser.prototype.loadRhythm = function () {
    var self = this;
    if (this._rhythm || this._rhythmLoading) { return Promise.resolve(); }
    this._rhythmLoading = true;
    return this._fetchJson(this.dataBase + 'rhythm.json')
      .then(function (d) { self._rhythm = d || {}; })
      .catch(function () { self._rhythm = {}; });
  };

  // Lazy-load the per-(hex x month-window) sidecar (h3_id -> {feb,may,jun}).
  // Loaded once on the first non-Pooled season selection (mirror of loadRhythm),
  // so the default page load is unchanged. Three calendar month-windows, NOT a
  // climate average — see manifest.seasonal.note.
  GeoBrowser.prototype.loadSeasons = function () {
    var self = this;
    if (this._seasons || this._seasonsLoading) { return Promise.resolve(); }
    this._seasonsLoading = true;
    return this._fetchJson(this.dataBase + 'seasons.json')
      .then(function (d) { self._seasons = d || {}; })
      .catch(function () { self._seasons = {}; });
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
        // POIs are zoom-gated; re-render when crossing the threshold and refresh
        // the inline "zoom in to reveal" hint so it auto-clears past the gate.
        if (self._layersOn.pois) { self._scheduleRender(); self._updatePoiHint(); }
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

  // Is the active field BOTH season-aware AND a non-Pooled window selected?
  GeoBrowser.prototype._seasonActive = function (field) {
    return !!(this._season && field && field.seasonal && field.seasonKey);
  };

  // Resolve a hex's value for a field, honouring the active month-window. For a
  // season-aware field with a non-Pooled season selected, read the per-season
  // value from the lazy seasons.json sidecar (short seasonKey); otherwise read
  // the inline pooled column. Missing sidecar entry -> null (no-data grey), never
  // 0 — honesty over a fake zero (e.g. a hex that failed that window's support gate).
  GeoBrowser.prototype._valueFor = function (d, field) {
    if (this._seasonActive(field)) {
      var rec = this._seasons && d.h3_id ? this._seasons[d.h3_id] : null;
      var per = rec ? rec[this._season] : null;
      var v = per ? per[field.seasonKey] : null;
      return v == null ? null : v;
    }
    return d[field.column];
  };

  // Domain (min/max) for a field. Prefer manifest stats; else scan hexes once.
  // Accepts either a field object or a bare column string (legacy callers). When
  // a month-window is active and the field is season-aware, the scan reads the
  // per-season sidecar values (each window has its own range), cached per
  // (column, season) so flipping windows recomputes the ramp correctly.
  GeoBrowser.prototype._domain = function (fieldOrColumn) {
    var field = (typeof fieldOrColumn === 'string')
      ? { column: fieldOrColumn } : fieldOrColumn;
    var column = field.column;
    var seasonOn = this._seasonActive(field);
    var cacheKey = seasonOn ? (column + '@' + this._season) : column;
    if (this._domainCache[cacheKey]) { return this._domainCache[cacheKey]; }
    // Categorical columns have no numeric domain.
    if (CATEGORICAL[column]) { return null; }
    var dom = null;

    // Score columns: manifest.score_stats[<preset>].{min,max} (never seasonal).
    if (!seasonOn && column.indexOf('score_') === 0) {
      var preset = column.slice('score_'.length);
      var ss = (this.manifest.score_stats || {})[preset];
      if (ss && ss.min != null && ss.max != null) { dom = [ss.min, ss.max]; }
    }
    if (!dom) {
      var lo = Infinity, hi = -Infinity;
      var hx = this._hexes;
      for (var i = 0; i < hx.length; i++) {
        var v = seasonOn ? this._valueFor(hx[i], field) : hx[i][column];
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
    this._domainCache[cacheKey] = dom;
    return dom;
  };

  // Normalise a raw value to [0,1] given the field's ramp semantics.
  // POLARITY: brighter / high end of the ramp always means MORE LIVEABLE.
  // When goodWhen === 'low' (penalty/cost metrics: pollution, heat, minutes-away,
  // industry, night-light) we invert the normalised t so the GOOD low values land
  // at the bright end. boolean true is always the "good" state per its goodWhen.
  GeoBrowser.prototype._normalise = function (field, v, dom) {
    var lowGood = field.goodWhen === 'low';
    if (field.kind === 'boolean') {
      var b = v ? 1 : 0;
      return lowGood ? 1 - b : b;
    }
    var t;
    if (field.kind === 'diverging') {
      // Map pivot -> 0.5; spread by the larger half-range so the ramp is honest.
      var pivot = field.pivot != null ? field.pivot : 0;
      var half = Math.max(pivot - dom[0], dom[1] - pivot) || 1;
      t = clamp01(0.5 + (v - pivot) / (2 * half));
    } else {
      var lo = dom[0], span = (dom[1] - dom[0]) || 1;
      t = clamp01((v - lo) / span);
    }
    return lowGood ? 1 - t : t; // low-is-good metrics: invert so good = bright
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
      // _arcLayer returns [glowLayer, arcLayer] (glow underlay + crisp arcs).
      if (al) { layers = layers.concat(al); }
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
    var seasonOn = this._seasonActive(field);
    // Categorical fields have no numeric domain (_domain returns null); use a
    // placeholder [0,1] so the updateTriggers array deref is safe. The fill
    // accessor ignores `dom` for categorical and reads the label->colour map.
    // When a month-window is active the domain is scanned over the per-season
    // values (a Feb weekend ratio spans a different range than the pooled one).
    var dom = this._domain(field) || [0, 1];
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
        var v = self._valueFor(d, field);
        // Null cells scale with the slider too, but stay clearly dimmer than
        // data cells so "no data" never reads as a value.
        var nullA = Math.round(NULL_ALPHA * (self._fillAlpha / FILL_ALPHA));
        if (v == null || (typeof v === 'number' && isNaN(v))) {
          return [NULL_COLOR[0], NULL_COLOR[1], NULL_COLOR[2], nullA];
        }
        // Categorical fields: fixed label->colour map (not a ramp).
        if (field.kind === 'categorical') {
          var cc = categoricalColor(column, v);
          if (!cc) { return [NULL_COLOR[0], NULL_COLOR[1], NULL_COLOR[2], nullA]; }
          return [cc[0], cc[1], cc[2], self._fillAlpha];
        }
        var t = self._normalise(field, v, dom);
        var c = rampColor(ramp, t);
        return [c[0], c[1], c[2], self._fillAlpha];
      },
      getElevation: function (d) {
        if (!extrude) { return 0; }
        var v = self._valueFor(d, field);
        if (v == null || (typeof v === 'number' && isNaN(v))) { return 0; }
        // Categorical fields carry no magnitude — flat extrusion (full height).
        if (field.kind === 'categorical') { return 1; }
        return self._normalise(field, v, dom);
      },
      updateTriggers: {
        getFillColor: [column, ramp, dom[0], dom[1], field.kind, this._fillAlpha, this._season, seasonOn],
        getElevation: [column, extrude, dom[0], dom[1], field.kind, this._season, seasonOn]
      }
      // NOTE: deliberately NO per-attribute `transitions` here. Animated
      // getFillColor/getElevation interpolation across 45k instanced hexes
      // doubles the attribute buffers and overruns software-WebGL (swiftshader)
      // with "GL_INVALID_OPERATION: glDrawElementsInstanced: Insufficient
      // buffer size", which breaks headless rendering. Recolouring 45k static
      // hexes is instant without it; camera moves still ease via _flyTo.
    });
  };

  // Scan arcs.json once for the log10(flow+1) range. arcs.json now holds
  // MILLION-scale flow values (measured ~807k–7.87M → logMin ≈ 5.907,
  // logMax ≈ 6.896), so the raw log scale would clamp every arc into a fat
  // ~10.6–12.4px band. We capture the live min/max here and map it linearly
  // into ~1.5–8px in getWidth (see _arcWidth), so the look survives if the
  // arc set is later regenerated with a different flow magnitude.
  GeoBrowser.prototype._computeArcDomain = function () {
    var data = this._arcs || [];
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < data.length; i++) {
      var f = data[i].flow || 1;
      var l = Math.log10(f + 1);
      if (l < lo) { lo = l; }
      if (l > hi) { hi = l; }
    }
    // Fallback to the measured Catalonia domain if arcs.json is empty/degenerate.
    this._arcLogMin = (lo === Infinity) ? 5.907 : lo;
    this._arcLogMax = (hi === -Infinity || hi === lo) ? this._arcLogMin + 1 : hi;
  };

  // Map a single arc's flow into a delicate 1.5–8px width via the log domain.
  GeoBrowser.prototype._arcWidth = function (d) {
    var lo = this._arcLogMin, hi = this._arcLogMax;
    if (lo == null) { this._computeArcDomain(); lo = this._arcLogMin; hi = this._arcLogMax; }
    var l = Math.log10((d.flow || 1) + 1);
    var t = clamp01((l - lo) / ((hi - lo) || 1));
    return 1.5 + t * 6.5; // thin (weak corridor) -> thick (strongest corridor)
  };

  GeoBrowser.prototype._arcLayer = function () {
    var self = this;
    var data = this._arcs || [];
    if (!data.length) { return null; }
    if (this._arcLogMin == null) { this._computeArcDomain(); }
    var common = {
      data: data,
      // greatCircle + a getHeight arch turns the flat hops back into tall
      // great-circle bridges (the original MITMA OD look, regression e05db0d).
      greatCircle: true,
      getHeight: 0.4,
      getSourcePosition: function (d) { return [d.source_lon, d.source_lat]; },
      getTargetPosition: function (d) { return [d.target_lon, d.target_lat]; },
      widthUnits: 'pixels'
    };
    // Subtle glow underlay: a wider, low-alpha twin of each arc so the bright
    // arcs read against the satellite/dark basemap without clogging.
    var glow = new deck.ArcLayer(Object.assign({}, common, {
      id: 'arcs-glow',
      pickable: false,
      getSourceColor: [0, 220, 255, 45],
      getTargetColor: [255, 0, 200, 45],
      getWidth: function (d) { return self._arcWidth(d) + 3; },
      updateTriggers: { getWidth: [this._arcLogMin, this._arcLogMax] }
    }));
    var arcs = new deck.ArcLayer(Object.assign({}, common, {
      id: 'arcs',
      pickable: true,
      getSourceColor: [0, 220, 255, 200],   // cyan
      getTargetColor: [255, 0, 200, 200],    // magenta
      getWidth: function (d) { return self._arcWidth(d); },
      updateTriggers: { getWidth: [this._arcLogMin, this._arcLogMax] }
    }));
    // Return both as a compound (glow under, crisp arcs on top).
    return [glow, arcs];
  };

  // POIs: one ScatterplotLayer per category so each carries its domain colour
  // and a category tag for the tooltip. Zoom-gated (hidden < z10.5) per spec §8.
  GeoBrowser.prototype._poiLayers = function () {
    var pois = this._pois || {};
    var z = this._viewState.zoom || 8;
    if (z < POI_ZOOM_GATE) { return []; }
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

  // ---- OSM-amenities zoom-gate hint (FIX 2) ---------------------------------
  // The POI layer is correctly z-gated (<POI_ZOOM_GATE it renders nothing), but
  // toggling the checkbox ON while zoomed out gives NO feedback, so it looks
  // broken. We surface a small inline hint next to the OSM-amenities control and
  // auto-clear it once the user zooms past the gate. We do NOT hijack the camera.
  // The hint reuses the existing `.dyn-hint` caption style from explore.css.
  GeoBrowser.prototype._poiHintEl = function () {
    if (this._poiHint) { return this._poiHint; }
    var doc = global.document;
    if (!doc) { return null; }
    var toggle = doc.getElementById('toggle-pois');
    if (!toggle) { return null; }
    // Mount inside the same .layer-row as the OSM-amenities checkbox, after the
    // existing caption, so it sits where the user is looking.
    var row = toggle.closest ? toggle.closest('.layer-row') : null;
    var el = doc.createElement('p');
    el.className = 'dyn-hint';
    el.id = 'pois-zoom-hint';
    el.setAttribute('role', 'status');
    (row || toggle.parentNode).appendChild(el);
    this._poiHint = el;
    return el;
  };

  // Refresh the hint to match (pois layer on?) x (zoomed past the gate?).
  GeoBrowser.prototype._updatePoiHint = function () {
    var el = this._poiHintEl();
    if (!el) { return; }
    var z = this._viewState.zoom || 8;
    var show = this._layersOn.pois && z < POI_ZOOM_GATE;
    if (show) {
      var pois = this._pois || {};
      var n = 0;
      Object.keys(pois).forEach(function (k) { n += (pois[k] || []).length; });
      var count = n || 322; // measured point-level amenity count
      el.textContent = 'Zoom in past z' + POI_ZOOM_GATE + ' to reveal the ' +
        count + ' point-level amenities.';
      el.classList.add('is-on');
    } else {
      el.classList.remove('is-on');
    }
  };

  // ---- Tooltip ---------------------------------------------------------------
  GeoBrowser.prototype._tooltip = function (info) {
    if (!info || !info.object) { return null; }
    var o = info.object;
    var id = info.layer && info.layer.id;

    if (id === 'hexes') {
      var field = this._activeField();
      var v = this._valueFor(o, field);
      var valueHtml;
      if (v == null || (typeof v === 'number' && isNaN(v))) {
        valueHtml = '<span class="tt-null">no data</span>';
      } else if (field.kind === 'boolean') {
        valueHtml = '<strong>' + (v ? 'yes' : 'no') + '</strong>';
      } else if (field.kind === 'categorical') {
        valueHtml = '<strong>' + esc(v) + '</strong>';
      } else {
        valueHtml = '<strong>' + formatNum(v) + '</strong>' + esc(field.unit || '');
      }
      // 24h rhythm sparkline (when loaded) — the heavy profile array lives in
      // the lazy rhythm.json sibling, NOT in hexes.json.
      var sparkHtml = '';
      var prof = this._rhythm && o.h3_id ? this._rhythm[o.h3_id] : null;
      if (prof && prof.length === 24) {
        sparkHtml = '<div class="tt-spark">' + sparkline(prof) +
          '<div class="tt-spark-cap">hour-of-day trip share (0–23h)</div></div>';
      }
      var ttWindow = this._seasonActive(field)
        ? '<div class="tt-window">' + esc(this._seasonLabel(this._season)) + '</div>' : '';
      return {
        html: '<div class="gb-tooltip">' +
          '<div class="tt-label">' + esc(field.label) + '</div>' +
          ttWindow +
          '<div class="tt-value">' + valueHtml + '</div>' +
          sparkHtml +
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
    var self = this;
    // Append the active month-window to the label so the reader always knows
    // which window they see (honest: a month-window, not a climate average).
    var windowSuffix = this._seasonActive(field) ? (' · ' + this._seasonLabel(this._season)) : '';

    // Categorical legend: one swatch per label present in the data (ordered by
    // the manifest's typology order when available, else the map's own order).
    if (field.kind === 'categorical') {
      var cmap = CATEGORICAL[field.column] || {};
      var order = (field.column === 'mobility_typology' && this.manifest.typology_labels)
        ? this.manifest.typology_labels : Object.keys(cmap);
      var present = {};
      for (var k = 0; k < this._hexes.length; k++) {
        var lv = self._valueFor(this._hexes[k], field);
        if (lv != null) { present[lv] = true; }
      }
      // Include known labels in the manifest order first, then any extra labels
      // actually present in the data (suffix variants / mixed-balanced), so the
      // legend never omits a class the map paints.
      var seen = {};
      var ordered = order.filter(function (lab) { seen[lab] = true; return present[lab]; });
      Object.keys(present).forEach(function (lab) { if (!seen[lab]) { ordered.push(lab); } });
      var cats = ordered.map(function (lab) {
        return { label: lab, color: categoricalColor(field.column, lab) || NULL_COLOR };
      });
      this.onLegend({
        label: field.label + windowSuffix, kind: 'categorical',
        categories: cats, nullColor: NULL_COLOR
      });
      return;
    }

    var dom = this._domain(field);
    var stops = [];
    var n = 6;
    for (var i = 0; i < n; i++) {
      stops.push(rampColor(field.ramp, i / (n - 1)));
    }
    // The ramp runs dark (low end) -> bright (high end). _normalise inverts
    // goodWhen:'low' metrics so the GOOD low raw value lands at the BRIGHT end,
    // so the legend must swap BOTH the numeric ends AND their word labels together
    // (previously only the numbers were swapped -> "1.20 µg/m³ · polluted").
    var swap = (field.goodWhen === 'low');
    var u = esc(field.unit);
    var loVal = formatNum(swap ? dom[1] : dom[0]) + u;
    var hiVal = formatNum(swap ? dom[0] : dom[1]) + u;
    var loLabel = swap ? field.highLabel : field.lowLabel;
    var hiLabel = swap ? field.lowLabel : field.highLabel;
    this.onLegend({
      label: field.label + windowSuffix,
      kind: field.kind,
      stops: stops,
      lowVal: loVal,
      highVal: hiVal,
      lowLabel: loLabel,
      highLabel: hiLabel,
      nullColor: NULL_COLOR
    });
  };

  // Human label for a season key, from the manifest windows (falls back to the
  // key). The honest "winter · Feb 2025" form — never a bare "winter".
  GeoBrowser.prototype._seasonLabel = function (key) {
    var windows = (this.manifest.seasonal && this.manifest.seasonal.windows) || [];
    for (var i = 0; i < windows.length; i++) {
      if (windows[i].key === key) { return windows[i].label; }
    }
    return key;
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
    // Selecting a rhythm/peak field lazy-loads the 24h profiles so the hover
    // tooltip can draw the sparkline; re-render once they arrive.
    var rhythmFields = ['am_peak_share', 'pm_peak_share', 'midday_share',
      'night_share', 'peak_hour_bucket'];
    if (rhythmFields.indexOf(fieldKey) !== -1 && !this._rhythm) {
      var self = this;
      this.loadRhythm().then(function () { self._render(); });
    }
  };

  // Select a month-window: '' = Pooled (default, reads inline hexes.json exactly
  // as before — zero behaviour change), else 'feb'|'may'|'jun'. The first
  // non-Pooled selection lazy-loads the seasons.json sidecar (mirror of rhythm),
  // then re-renders. Season-aware fields re-point to that window; non-seasonal
  // fields ignore _season entirely (the Season dropdown should be greyed for them).
  GeoBrowser.prototype.setSeason = function (key) {
    key = key || '';
    if (key === this._season) { return; }
    this._season = key;
    var self = this;
    if (key && !this._seasons) {
      this.loadSeasons().then(function () { self._render(); });
    } else {
      this._render();
    }
  };

  // Whether the active field responds to the Season dropdown (drives the UI's
  // enabled/greyed state + the 'pooled across all 89 days' hint).
  GeoBrowser.prototype.activeFieldIsSeasonal = function () {
    var f = FIELDS[this._fieldKey];
    return !!(f && f.seasonal);
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
        .then(function (d) { self._arcs = d || []; self._computeArcDomain(); self._render(); })
        .catch(function (e) { self._layersOn.arcs = false; throw e; });
    }
    if (on && key === 'pois' && !this._pois) {
      return this._fetchJson(this.dataBase + 'pois.json')
        .then(function (d) { self._pois = d || {}; self._render(); self._updatePoiHint(); })
        .catch(function (e) { self._layersOn.pois = false; throw e; });
    }
    this._render();
    // Keep the OSM-amenities zoom hint in sync on every pois toggle (clears on off).
    if (key === 'pois') { this._updatePoiHint(); }
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
  // Inline SVG sparkline (filled area) for a 24-value [0..maxshare] profile.
  function sparkline(vals) {
    var W = 168, H = 34, n = vals.length;
    var max = 0;
    for (var i = 0; i < n; i++) { if (vals[i] > max) { max = vals[i]; } }
    if (max <= 0) { max = 1; }
    var step = W / (n - 1);
    var pts = [];
    for (var j = 0; j < n; j++) {
      var x = (j * step).toFixed(1);
      var y = (H - (vals[j] / max) * (H - 4) - 2).toFixed(1);
      pts.push(x + ',' + y);
    }
    var line = 'M' + pts.join(' L');
    var area = line + ' L' + W + ',' + H + ' L0,' + H + ' Z';
    return '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H +
      '" preserveAspectRatio="none" aria-hidden="true">' +
      '<path d="' + area + '" fill="rgba(136,212,255,0.22)"/>' +
      '<path d="' + line + '" fill="none" stroke="#88d4ff" stroke-width="1.4"/>' +
      '</svg>';
  }

  GeoBrowser.RAMPS = RAMPS;
  GeoBrowser.FIELDS = FIELDS;
  GeoBrowser.CATEGORICAL = CATEGORICAL;
  GeoBrowser.BASEMAPS = BASEMAPS;
  GeoBrowser.DEFAULT_FILL_ALPHA = FILL_ALPHA;
  GeoBrowser.rampColor = rampColor;
  GeoBrowser.NULL_COLOR = NULL_COLOR;
  global.GeoBrowser = GeoBrowser;
})(window);
