(function () {
  "use strict";

  /*
   * Main browser behavior for the infrastructure map.
   *
   * The HTML file owns page structure, the data/ directory owns generated
   * JSON/GeoJSON, and this file owns safe rendering, filtering, layer toggles,
   * and popups. Large optional layers are lazy-fetched so the first map view
   * stays responsive.
   */

  const MAX_TEXT_LENGTH = 500;
  const MAX_DATA_CENTER_RECORDS = 2000;
  const MAX_POWER_RECORDS = 25000;
  const MAX_WATER_RECORDS = 1000;
  const MAX_INITIAL_DATA_CHARS = 1_500_000;
  const MAX_POWER_TILE_INDEX_CHARS = 350_000;
  const MAX_POWER_TILE_CHARS = 1_200_000;
  const MAX_DETAIL_DATA_CHARS = 900_000;
  const MAX_RENDERED_POWER_MARKERS = 1600;
  const POWER_BOUNDS_PADDING = 0.35;
  const MAX_ANIMATED_MOVE_METERS = 450_000;
  const SELECTED_RESULT_ZOOM = 8;
  const VIEWPORT_REFRESH_DELAY_MS = 90;
  const POWER_REFRESH_DELAY_MS = 110;
  const HEAT_REFRESH_DELAY_MS = 80;
  const BUBBLE_SPLIT_TRANSITION_MS = 460;
  const TOUCHPAD_ZOOM_STEP_DELTA = 90;
  const TOUCHPAD_ZOOM_COOLDOWN_MS = 220;
  const TOUCHPAD_ZOOM_RESET_MS = 180;
  const HEAT_BASE_RADIUS = 34;
  const HEAT_BASE_BLUR = 30;
  const HEAT_BASE_ZOOM = 4;
  const HEAT_RADIUS_SCALE = 1.16;

  const VISIBILITY = Object.freeze({
    dataHubMaxZoom: 5,
    dataMarkerMinZoom: 6,
    connectorMinZoom: 7,
    nuclearMinZoom: 5,
    waterMinZoom: 6,
    powerMinZoom: 7,
    heatMaxZoom: 7
  });

  const DATA_ENDPOINTS = Object.freeze({
    summary: "data/map-summary.json",
    dataCenterHubs: "data/data-center-hubs.geojson",
    dataCenters: "data/data-centers.geojson",
    nuclearPlants: "data/nuclear-plants.geojson",
    powerTileIndex: "data/power-tiles/index.json",
    waterSources: "data/water-sources.geojson"
  });

  const statusStyles = Object.freeze({
    Operating: { color: "#005ea8", glow: "rgba(0, 94, 168, 0.28)", tag: "OP" },
    "Under construction": { color: "#8a5a00", glow: "rgba(138, 90, 0, 0.28)", tag: "UC" },
    Planned: { color: "#b91c5c", glow: "rgba(185, 28, 92, 0.27)", tag: "PL" },
    Proposed: { color: "#5b21b6", glow: "rgba(91, 33, 182, 0.27)", tag: "PR" },
    Permitted: { color: "#04766d", glow: "rgba(4, 118, 109, 0.24)", tag: "PM" },
    Cancelled: { color: "#475569", glow: "rgba(71, 85, 105, 0.24)", tag: "CA" },
    Other: { color: "#334155", glow: "rgba(51, 65, 85, 0.2)", tag: "DC" }
  });

  const proximityStyles = Object.freeze({
    nuclear: { color: "#15803d", dashArray: "6 8", opacity: 0.72 },
    power: { color: "#c2410c", dashArray: "2 8", opacity: 0.68 },
    water: { color: "#0369a1", dashArray: "1 9", opacity: 0.7 }
  });

  const HEAT_GRADIENT = Object.freeze({
    0.18: "#22d3ee",
    0.38: "#22c55e",
    0.58: "#facc15",
    0.78: "#fb7185",
    1.0: "#a855f7"
  });

  const TILE_LAYER_OPTIONS = Object.freeze({
    maxZoom: 19,
    updateWhenIdle: false,
    updateWhenZooming: true,
    updateInterval: 140,
    keepBuffer: 4
  });

  const US_BOUNDS = L.latLngBounds(
    [24.396308, -125.0],
    [49.384358, -66.93457]
  );

  // Cached references to the controls defined in the HTML.
  const searchInput = document.getElementById("siteSearch");
  const shownCount = document.getElementById("shownCount");
  const showNuclear = document.getElementById("showNuclear");
  const showPower = document.getElementById("showPower");
  const showWater = document.getElementById("showWater");
  const showConnectors = document.getElementById("showConnectors");
  const showHeat = document.getElementById("showHeat");
  const detailPanel = document.getElementById("detailPanel");
  const resultsList = document.getElementById("resultsList");
  const resultsMeta = document.getElementById("resultsMeta");
  const summaryText = document.getElementById("summaryText");
  const dataCapacityMetric = document.getElementById("dataCapacityMetric");
  const nuclearCapacityMetric = document.getElementById("nuclearCapacityMetric");
  const powerCountMetric = document.getElementById("powerCountMetric");
  const dataCountDetail = document.getElementById("dataCountDetail");
  const powerCountDetail = document.getElementById("powerCountDetail");
  const waterCountDetail = document.getElementById("waterCountDetail");

  let rawData = {};
  let dataCenterHubs = [];
  let dataCenters = [];
  let nuclearPlants = [];
  let waterSources = [];
  let powerTileIndex = null;
  let powerTileIndexRequest = null;
  const detailShardCache = new Map();
  const dataCenterMarkerCache = new Map();
  const dataCenterLineCache = new Map();
  const hubMarkerCache = new Map();
  const hubBuildCache = new Map();
  const powerMarkerCache = new Map();
  const powerTileCache = new Map();
  const reducedMotionMedia = window.matchMedia("(prefers-reduced-motion: reduce)");
  let dataCenterRenderKey = "";
  let powerRenderKey = "";
  let powerRequestKey = "";
  let powerRefreshFrame = 0;

  const map = L.map("map", {
    maxBounds: US_BOUNDS,
    maxBoundsViscosity: 1.0,
    minZoom: 4,
    maxZoom: 10,
    zoomControl: false,
    preferCanvas: true,
    zoomAnimation: !prefersReducedMotion(),
    markerZoomAnimation: !prefersReducedMotion(),
    fadeAnimation: !prefersReducedMotion(),
    keyboard: true,
    keyboardPanDelta: 70,
    scrollWheelZoom: false,
    wheelDebounceTime: 70,
    wheelPxPerZoomLevel: 90,
    bounceAtZoomLimits: false
  });

  map.fitBounds(US_BOUNDS, { padding: [20, 20] });

  // Tile layers are external but version-stable; the CSP only allows vetted hosts.
  const baseLayers = {
    Color: L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
      ...TILE_LAYER_OPTIONS,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
    }),
    Light: L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      ...TILE_LAYER_OPTIONS,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
    }),
    Dark: L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      ...TILE_LAYER_OPTIONS,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
    }),
    Satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      ...TILE_LAYER_OPTIONS,
      attribution: "Tiles &copy; Esri"
    })
  };

  const dcHubLayer = L.layerGroup();
  const dcCluster = L.markerClusterGroup({
    showCoverageOnHover: false,
    spiderfyOnMaxZoom: true,
    disableClusteringAtZoom: 8,
    removeOutsideVisibleBounds: false,
    animate: false,
    animateAddingMarkers: false,
    maxClusterRadius: 44,
    chunkedLoading: true,
    chunkInterval: 120,
    chunkDelay: 35,
    iconCreateFunction: (cluster) => clusterIcon(cluster.getChildCount(), "sites")
  });

  const nuclearLayer = L.layerGroup();
  const waterLayer = L.layerGroup();
  const connectorRenderer = L.canvas({ padding: 1.0 });
  const connectorLayer = L.layerGroup();

  let powerCluster = null;
  let heatLayer = null;
  let heatPoints = null;
  let refreshFrame = 0;
  let viewportRefreshTimer = 0;
  let heatRefreshTimer = 0;
  let powerRefreshTimer = 0;
  let dataBubbleTransitionTimer = 0;
  let dataBubbleTransitionMode = "";
  let zoomStartLevel = 0;
  let touchpadZoomDelta = 0;
  let touchpadZoomResetTimer = 0;
  let lastTouchpadZoomAt = 0;
  let isZooming = false;

  baseLayers.Light.addTo(map);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  L.control.layers(baseLayers, null, { position: "bottomright", collapsed: true }).addTo(map);

  map.addLayer(dcHubLayer);

  bindControls();
  showLoadingState();
  initializeMapData();

  async function initializeMapData() {
    try {
      const [summary, hubGeoJson, dataCenterGeoJson, nuclearGeoJson, waterGeoJson] = await Promise.all([
        fetchJson(DATA_ENDPOINTS.summary, MAX_INITIAL_DATA_CHARS),
        fetchJson(DATA_ENDPOINTS.dataCenterHubs, MAX_INITIAL_DATA_CHARS),
        fetchJson(DATA_ENDPOINTS.dataCenters, MAX_INITIAL_DATA_CHARS),
        fetchJson(DATA_ENDPOINTS.nuclearPlants, MAX_INITIAL_DATA_CHARS),
        fetchJson(DATA_ENDPOINTS.waterSources, MAX_INITIAL_DATA_CHARS)
      ]);

      rawData = normalizeSummary(summary);
      dataCenterHubs = normalizeGeoJsonCollection(hubGeoJson, normalizeDataCenterHub, MAX_DATA_CENTER_RECORDS);
      dataCenters = normalizeGeoJsonCollection(dataCenterGeoJson, normalizeDataCenter, MAX_DATA_CENTER_RECORDS);
      nuclearPlants = normalizeGeoJsonCollection(nuclearGeoJson, normalizePowerPlant, MAX_WATER_RECORDS);
      waterSources = normalizeGeoJsonCollection(waterGeoJson, normalizeWaterSource, MAX_WATER_RECORDS);

      updateGeneratedMetrics(rawData);
      showOverviewState(rawData);
      buildNuclearLayer();
      buildWaterLayer();
      refreshDataCenterLayer();
      syncLayerToggles();
      fitToData();

      if (rawData.generatedAt) {
        map.attributionControl.addAttribution(`Generated ${safeText(rawData.generatedAt)}`);
      }
      map.on("zoomstart", handleZoomStart);
      map.on("zoomend", handleZoomEnd);
      map.on("moveend", scheduleViewportLayerRefresh);
    } catch (error) {
      showDataError(error);
    }
  }

  function handleZoomStart() {
    isZooming = true;
    zoomStartLevel = map.getZoom();
    stopDataBubbleTransition({ refresh: false });
    map.getContainer().classList.add("map-transitioning");
    if (viewportRefreshTimer) {
      window.clearTimeout(viewportRefreshTimer);
      viewportRefreshTimer = 0;
    }
    if (heatRefreshTimer) {
      window.clearTimeout(heatRefreshTimer);
      heatRefreshTimer = 0;
    }
    cancelPowerLayerRefresh();
  }

  function handleZoomEnd() {
    isZooming = false;
    map.getContainer().classList.remove("map-transitioning");
    const transitionMode = dataBubbleTransitionForZoom(zoomStartLevel, map.getZoom());
    if (transitionMode) {
      startDataBubbleTransition(transitionMode);
    }
    scheduleHeatLayerRefresh();
    scheduleViewportLayerRefresh();
  }

  function dataBubbleTransitionForZoom(startZoom, endZoom) {
    if (prefersReducedMotion()) return "";
    if (startZoom < VISIBILITY.dataMarkerMinZoom && endZoom >= VISIBILITY.dataMarkerMinZoom) {
      return "splitting";
    }
    if (startZoom >= VISIBILITY.dataMarkerMinZoom && endZoom < VISIBILITY.dataMarkerMinZoom) {
      return "merging";
    }
    return "";
  }

  function startDataBubbleTransition(mode) {
    stopDataBubbleTransition({ refresh: false });
    dataBubbleTransitionMode = mode;
    map.getContainer().classList.add(`data-bubbles-${mode}`);
    refreshDataCenterLayer({ renderResults: false });
    syncLayerToggles();
    dataBubbleTransitionTimer = window.setTimeout(() => {
      stopDataBubbleTransition({ refresh: true });
    }, BUBBLE_SPLIT_TRANSITION_MS);
  }

  function stopDataBubbleTransition(options = {}) {
    if (dataBubbleTransitionTimer) {
      window.clearTimeout(dataBubbleTransitionTimer);
      dataBubbleTransitionTimer = 0;
    }
    if (dataBubbleTransitionMode) {
      map.getContainer().classList.remove(`data-bubbles-${dataBubbleTransitionMode}`);
    }
    dataBubbleTransitionMode = "";
    if (options.refresh) {
      refreshDataCenterLayer({ renderResults: false });
      syncLayerToggles();
    }
  }

  function scheduleViewportLayerRefresh() {
    if (isZooming) return;
    if (viewportRefreshTimer) window.clearTimeout(viewportRefreshTimer);
    viewportRefreshTimer = window.setTimeout(() => {
      viewportRefreshTimer = 0;
      if (isZooming) return;
      refreshViewportLayers();
    }, VIEWPORT_REFRESH_DELAY_MS);
  }

  function refreshViewportLayers() {
    refreshDataCenterLayer({ renderResults: false });
    syncLayerToggles();
  }

  function bindControls() {
    bindTouchpadZoom();
    searchInput.addEventListener("input", scheduleDataCenterRefresh);
    resultsList.addEventListener("click", handleResultActivation);
    resultsList.addEventListener("keydown", handleResultActivation);

    document.querySelectorAll("#statusFilters input").forEach((input) => {
      input.addEventListener("change", () => {
        syncStatusChipState();
        scheduleDataCenterRefresh();
      });
    });

    [showNuclear, showPower, showWater, showConnectors, showHeat].forEach((input) => {
      input.addEventListener("change", () => {
        if (input === showConnectors) {
          scheduleDataCenterRefresh();
        }
        syncLayerToggles();
      });
    });
  }

  function bindTouchpadZoom() {
    map.getContainer().addEventListener("wheel", handleTouchpadZoom, { passive: false });
  }

  function handleTouchpadZoom(event) {
    if (!shouldHandleTouchpadZoom(event)) return;
    event.preventDefault();

    const delta = normalizedWheelDelta(event);
    if (!delta) return;

    touchpadZoomDelta = clamp(touchpadZoomDelta + delta, -TOUCHPAD_ZOOM_STEP_DELTA, TOUCHPAD_ZOOM_STEP_DELTA);
    if (touchpadZoomResetTimer) window.clearTimeout(touchpadZoomResetTimer);
    touchpadZoomResetTimer = window.setTimeout(() => {
      touchpadZoomDelta = 0;
      touchpadZoomResetTimer = 0;
    }, TOUCHPAD_ZOOM_RESET_MS);

    const now = currentTimeMs();
    if (Math.abs(touchpadZoomDelta) < TOUCHPAD_ZOOM_STEP_DELTA || now - lastTouchpadZoomAt < TOUCHPAD_ZOOM_COOLDOWN_MS) {
      return;
    }

    const direction = touchpadZoomDelta < 0 ? 1 : -1;
    touchpadZoomDelta = 0;
    lastTouchpadZoomAt = now;
    zoomByButtonStep(direction);
  }

  function shouldHandleTouchpadZoom(event) {
    if (event.defaultPrevented || event.altKey || event.shiftKey) return false;
    if (event.target && typeof event.target.closest === "function" && event.target.closest(".ui-shell")) {
      return false;
    }
    if (event.ctrlKey || event.metaKey) return true;
    return Math.abs(event.deltaY) > Math.abs(event.deltaX) && Math.abs(event.deltaY) >= 4;
  }

  function normalizedWheelDelta(event) {
    const multiplier = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1;
    return event.deltaY * multiplier;
  }

  function currentTimeMs() {
    return window.performance && typeof window.performance.now === "function"
      ? window.performance.now()
      : Date.now();
  }

  function zoomByButtonStep(direction) {
    const options = { animate: !prefersReducedMotion() };
    if (direction > 0) {
      map.zoomIn(1, options);
    } else {
      map.zoomOut(1, options);
    }
  }

  async function fetchJson(path, maxChars) {
    const url = new URL(path, window.location.href);
    if (url.origin !== window.location.origin) {
      throw new Error("Map data must be loaded from this site.");
    }

    const response = await fetch(url.href, {
      cache: "force-cache",
      credentials: "same-origin",
      referrerPolicy: "strict-origin-when-cross-origin"
    });

    if (!response.ok) {
      throw new Error("Map data is unavailable.");
    }

    const contentLength = Number(response.headers.get("Content-Length"));
    if (Number.isFinite(contentLength) && contentLength > maxChars) {
      throw new Error("Map data response is too large.");
    }

    const text = await response.text();
    if (text.length > maxChars) {
      throw new Error("Map data response is too large.");
    }

    try {
      return JSON.parse(text);
    } catch {
      throw new Error("Map data is not valid JSON.");
    }
  }

  function normalizeSummary(summary) {
    const item = summary && typeof summary === "object" ? summary : {};
    return {
      generatedAt: safeText(item.generatedAt, 80),
      recordCounts: item.recordCounts && typeof item.recordCounts === "object" ? item.recordCounts : {},
      capacityMw: item.capacityMw && typeof item.capacityMw === "object" ? item.capacityMw : {},
      dataFiles: item.dataFiles && typeof item.dataFiles === "object" ? item.dataFiles : {},
      sources: Array.isArray(item.sources) ? item.sources.slice(0, 10) : []
    };
  }

  function normalizeGeoJsonCollection(collection, normalizer, maxRecords) {
    if (!collection || collection.type !== "FeatureCollection" || !Array.isArray(collection.features)) {
      throw new Error("Map data has an invalid GeoJSON shape.");
    }

    return collection.features
      .slice(0, maxRecords)
      .map(featureToRecord)
      .map(normalizer)
      .filter((item) => item.latitude !== null && item.longitude !== null);
  }

  function normalizePowerTileIndex(payload) {
    const item = payload && typeof payload === "object" ? payload : {};
    const tiles = Array.isArray(item.tiles) ? item.tiles : [];
    return tiles
      .map((tile) => {
        const source = tile && typeof tile === "object" ? tile : {};
        const bounds = Array.isArray(source.bounds) ? source.bounds.map(Number) : [];
        const file = safePowerTilePath(source.file);
        if (bounds.length !== 4 || bounds.some((value) => !Number.isFinite(value)) || !file) {
          return null;
        }
        return {
          key: safeText(source.key, 120) || file,
          file,
          bounds,
          count: safeWholeNumber(source.count)
        };
      })
      .filter(Boolean);
  }

  function featureToRecord(feature) {
    const item = feature && typeof feature === "object" ? feature : {};
    const geometry = item.geometry && typeof item.geometry === "object" ? item.geometry : {};
    const coordinates = Array.isArray(geometry.coordinates) ? geometry.coordinates : [];
    const properties = item.properties && typeof item.properties === "object" ? item.properties : {};
    return {
      ...properties,
      longitude: coordinates[0],
      latitude: coordinates[1]
    };
  }

  function updateGeneratedMetrics(summary) {
    const counts = summary.recordCounts || {};
    const capacity = summary.capacityMw || {};
    const dataCenterCount = safeWholeNumber(counts.dataCenters);
    const nuclearCount = safeWholeNumber(counts.nuclearPlants);
    const powerCount = safeWholeNumber(counts.powerPlants);
    const waterCount = safeWholeNumber(counts.waterSources);
    const dataCapacity = safeNumber(capacity.dataCenters);
    const nuclearCapacity = safeNumber(capacity.nuclearPlants);

    if (summaryText) {
      summaryText.textContent = [
        `${numberFormat(dataCenterCount)} public data-center facility records`,
        `${numberFormat(nuclearCount)} nuclear operating or pipeline records`,
        `${numberFormat(powerCount)} power-plant records`,
        `${numberFormat(waterCount)} named water-source references`
      ].join(", ") + ".";
    }

    if (dataCapacityMetric) dataCapacityMetric.textContent = `${numberFormat(dataCapacity / 1000, 1)} GW`;
    if (nuclearCapacityMetric) nuclearCapacityMetric.textContent = `${numberFormat(nuclearCapacity / 1000, 1)} GW`;
    if (powerCountMetric) powerCountMetric.textContent = numberFormat(powerCount);
    if (dataCountDetail) dataCountDetail.textContent = `${numberFormat(dataCenterCount)} public facility records`;
    if (powerCountDetail) powerCountDetail.textContent = `${numberFormat(powerCount)} operating and planned records`;
    if (waterCountDetail) waterCountDetail.textContent = `${numberFormat(waterCount)} named reference points`;
  }

  function showLoadingState() {
    const title = createElement("div", "detail-title");
    title.append(
      createElement("h2", "", "Loading"),
      createElement("span", "pill", "Data")
    );
    shownCount.textContent = "0";
    renderResultMessage("Loading data-center results.", "Loading");
    detailPanel.replaceChildren(
      title,
      createElement("div", "detail-body", "Loading map data.")
    );
  }

  function showOverviewState(summary) {
    const counts = summary.recordCounts || {};
    const title = createElement("div", "detail-title");
    const details = createElement("dl", "detail-list");

    title.append(
      createElement("h2", "", "National View"),
      createElement("span", "pill", "Landscape")
    );
    appendDetailRows(details, [
      ["Data centers", `${numberFormat(safeWholeNumber(counts.dataCenters))} public facility records`],
      ["Power plants", `${numberFormat(safeWholeNumber(counts.powerPlants))} operating and planned records`],
      ["Water", `${numberFormat(safeWholeNumber(counts.waterSources))} named reference points`]
    ]);
    detailPanel.replaceChildren(
      title,
      createElement(
        "div",
        "detail-body",
        "Market-hub placeholders are no longer included. Facility markers use public records, and proximity lines compare each data center with its nearest nuclear site, power plant, and named water-source reference."
      ),
      details
    );
  }

  function showDataError(error) {
    const title = createElement("div", "detail-title");
    title.append(
      createElement("h2", "", "Data unavailable"),
      createElement("span", "pill", "Error")
    );
    detailPanel.replaceChildren(
      title,
      createElement("div", "detail-body", "Map data could not be loaded. Start the Python map server and refresh the page.")
    );
    shownCount.textContent = "0";
    renderResultMessage("Results unavailable.", "Error");
    console.error(error);
  }

  function renderResultMessage(message, metaText) {
    if (resultsMeta) resultsMeta.textContent = metaText;
    if (!resultsList) return;
    const item = createElement("li", "result-empty", message);
    resultsList.replaceChildren(item);
  }

  function renderResultsList(sites) {
    if (!resultsList) return;

    if (!sites.length) {
      shownCount.textContent = "0";
      renderResultMessage("No data centers match the current filters.", "0 shown");
      return;
    }

    const fragment = document.createDocumentFragment();
    sites.forEach((site) => {
      const item = document.createElement("li");
      item.append(dataCenterResultButton(site));
      fragment.append(item);
    });

    resultsList.replaceChildren(fragment);
    if (resultsMeta) resultsMeta.textContent = `${numberFormat(sites.length)} shown`;
  }

  function dataCenterResultButton(site) {
    const button = document.createElement("button");
    const meta = [
      site.status_group,
      site.location || [site.city, site.state].filter(Boolean).join(", "),
      site.capacity_label || mwLabel(site.capacity_mw)
    ].filter(Boolean).join(" / ");
    const context = [
      nearestResultText(site, "nuclear", "Nuclear"),
      nearestResultText(site, "power", "Power"),
      nearestResultText(site, "water", "Water")
    ].filter(Boolean).join(" / ");

    button.type = "button";
    button.className = "result-button";
    button.dataset.siteKey = site.key;
    button.setAttribute("aria-label", dataCenterAccessibleLabel(site));
    button.append(createElement("span", "result-name", site.name || "Unnamed facility"));
    if (meta) button.append(createElement("span", "result-meta", meta));
    if (context) button.append(createElement("span", "result-context", context));
    return button;
  }

  function handleResultActivation(event) {
    if (event.type === "keydown" && !["Enter", " "].includes(event.key)) return;
    const button = event.target.closest(".result-button");
    if (!button || !resultsList.contains(button)) return;

    const site = dataCenters.find((item) => item.key === button.dataset.siteKey);
    if (!site) return;

    event.preventDefault();
    selectDataCenter(site);
  }

  function nearestResultText(site, kind, label) {
    const name = site[`nearest_${kind}_name`];
    const distance = site[`nearest_${kind}_distance_mi`];
    if (!name || !Number.isFinite(distance)) return "";
    return `${label}: ${name}, ${numberFormat(distance, 1)} mi`;
  }

  function dataCenterAccessibleLabel(site) {
    return [
      site.name || "Unnamed data-center facility",
      `${site.status_group} status`,
      site.location || [site.city, site.state].filter(Boolean).join(", "),
      site.capacity_label || mwLabel(site.capacity_mw),
      nearestResultText(site, "nuclear", "Nearest nuclear"),
      nearestResultText(site, "power", "Nearest power"),
      nearestResultText(site, "water", "Nearest water")
    ].filter(Boolean).join(". ");
  }

  function selectDataCenter(site) {
    const latLng = [site.latitude, site.longitude];
    const targetZoom = Math.max(map.getZoom(), SELECTED_RESULT_ZOOM);
    updateDetail(site, "data-center");
    detailPanel.focus({ preventScroll: true });

    const openSelected = () => {
      refreshDataCenterLayer({ renderResults: false });
      syncLayerToggles();
      const marker = getDataCenterMarker(site);
      const openPopup = () => {
        marker.openPopup();
        openRecordDetails(marker, site, "data-center");
        detailPanel.focus({ preventScroll: true });
      };

      if (typeof dcCluster.zoomToShowLayer === "function") {
        dcCluster.zoomToShowLayer(marker, openPopup);
        return;
      }

      openPopup();
    };

    if (map.getZoom() < targetZoom || map.distance(map.getCenter(), L.latLng(latLng)) > 160) {
      moveToFeature(latLng, targetZoom, openSelected);
      return;
    }

    openSelected();
  }

  function moveToFeature(latLng, zoom, onComplete) {
    let completed = false;
    const distanceMeters = map.distance(map.getCenter(), L.latLng(latLng));
    const complete = () => {
      if (completed) return;
      completed = true;
      onComplete();
    };

    map.once("moveend", complete);
    if (prefersReducedMotion() || distanceMeters > MAX_ANIMATED_MOVE_METERS) {
      map.setView(latLng, zoom, { animate: false });
      window.setTimeout(complete, 0);
      return;
    }

    map.flyTo(latLng, zoom, { duration: 0.45 });
  }

  function prefersReducedMotion() {
    return reducedMotionMedia.matches;
  }

  function normalizeDataCenter(site) {
    const normalized = normalizeSharedRecord(site);
    normalized.id = safeText(site.id, 160);
    normalized.developer = safeText(site.developer);
    normalized.status = safeText(site.status);
    normalized.status_group = normalizeStatusGroup(site.status_group);
    normalized.category = safeText(site.category);
    normalized.facility_type = safeText(site.facility_type);
    normalized.ai_classification = safeText(site.ai_classification);
    normalized.confidence = safeText(site.confidence);
    normalized.capacity_mw = safeNumber(site.capacity_mw);
    normalized.operational_capacity_mw = safeNumber(site.operational_capacity_mw);
    normalized.planned_capacity_mw = safeNumber(site.planned_capacity_mw);
    normalized.capacity_label = safeText(site.capacity_label);
    normalized.landscape_weight = safeNumber(site.landscape_weight);
    normalized.location = safeText(site.location);
    normalized.city = safeText(site.city);
    normalized.county = safeText(site.county);
    normalized.state = safeText(site.state, 10);
    normalized.precision = safeText(site.precision);
    normalized.powered_by = safeText(site.powered_by);
    normalized.energy_source = safeText(site.energy_source);
    normalized.utility = safeText(site.utility);
    normalized.onsite_generation_mw = safeNumber(site.onsite_generation_mw);
    normalized.water_cooling_type = safeText(site.water_cooling_type);
    normalized.water_reported_mgd = safeNumber(site.water_reported_mgd);
    normalized.water_notes = safeText(site.water_notes);
    normalized.community_status = safeText(site.community_status);
    normalized.investment_usd = safeNumber(site.investment_usd);
    normalized.land_acres = safeNumber(site.land_acres);
    normalized.jobs_construction = safeNumber(site.jobs_construction);
    normalized.jobs_permanent = safeNumber(site.jobs_permanent);
    normalized.source_count = safeNumber(site.source_count);

    addNearestFields(normalized, site, "nuclear");
    addNearestFields(normalized, site, "power");
    addNearestFields(normalized, site, "water");

    const fallbackSearch = [
      normalized.name,
      normalized.developer,
      normalized.status,
      normalized.status_group,
      normalized.category,
      normalized.location,
      normalized.city,
      normalized.county,
      normalized.state,
      normalized.powered_by,
      normalized.energy_source,
      normalized.utility,
      normalized.water_cooling_type,
      normalized.nearest_nuclear_name,
      normalized.nearest_power_name,
      normalized.nearest_water_name,
      normalized.notes
    ].join(" ").toLowerCase();
    normalized.searchText = safeText(site.search, 1600).toLowerCase() || fallbackSearch;

    return normalized;
  }

  function normalizeDataCenterHub(hub) {
    const normalized = normalizeSharedRecord(hub);
    normalized.count = safeWholeNumber(hub.count);
    normalized.capacity_mw = safeNumber(hub.capacity_mw);
    normalized.states = safeText(hub.states, 80);
    normalized.status_counts = hub.status_counts && typeof hub.status_counts === "object"
      ? hub.status_counts
      : {};
    return normalized;
  }

  function normalizePowerPlant(plant) {
    const normalized = normalizeSharedRecord(plant);
    normalized.plant_id = safeText(plant.plant_id, 80);
    normalized.operator = safeText(plant.operator);
    normalized.state = safeText(plant.state, 10);
    normalized.county = safeText(plant.county);
    normalized.capacity_mw = safeNumber(plant.capacity_mw);
    normalized.status_group = normalizeStatusGroup(plant.status_group);
    normalized.statuses = safeText(plant.statuses);
    normalized.technologies = safeText(plant.technologies);
    normalized.energy_sources = safeText(plant.energy_sources);
    normalized.type = safeText(plant.type || "Power plant");
    normalized.is_nuclear = plant.is_nuclear === true || safeText(plant.is_nuclear).toLowerCase() === "true";
    return normalized;
  }

  function normalizeWaterSource(source) {
    const normalized = normalizeSharedRecord(source);
    normalized.type = safeText(source.type);
    normalized.state = safeText(source.state, 10);
    return normalized;
  }

  function normalizeSharedRecord(record) {
    const item = record && typeof record === "object" ? record : {};
    return {
      key: safeText(item.key || item.id || item.plant_id || item.name, 180),
      detail_file: safeDataPath(item.detail_file),
      detailLoaded: false,
      name: safeText(item.name),
      latitude: safeCoordinate(item.latitude, -90, 90),
      longitude: safeCoordinate(item.longitude, -180, 180),
      source: safeText(item.source),
      source_url: safeText(item.source_url),
      notes: safeText(item.notes)
    };
  }

  function safeDataPath(value) {
    const text = safeText(value, 240).replace(/\\/g, "/");
    if (!text.startsWith("data/details/") || !text.endsWith(".json") || text.includes("..")) {
      return "";
    }
    return text;
  }

  function safePowerTilePath(value) {
    const text = safeText(value, 240).replace(/\\/g, "/");
    if (!text.startsWith("data/power-tiles/") || !text.endsWith(".geojson") || text.includes("..")) {
      return "";
    }
    return text;
  }

  function normalizeStatusGroup(status) {
    const text = safeText(status);
    return Object.prototype.hasOwnProperty.call(statusStyles, text) ? text : "Other";
  }

  function addNearestFields(target, source, kind) {
    target[`nearest_${kind}_name`] = safeText(source[`nearest_${kind}_name`]);
    target[`nearest_${kind}_state`] = safeText(source[`nearest_${kind}_state`], 30);
    target[`nearest_${kind}_type`] = safeText(source[`nearest_${kind}_type`], 80);
    target[`nearest_${kind}_source`] = safeText(source[`nearest_${kind}_source`], 80);
    target[`nearest_${kind}_distance_mi`] = safeNumber(source[`nearest_${kind}_distance_mi`]);
    target[`nearest_${kind}_capacity_mw`] = safeNumber(source[`nearest_${kind}_capacity_mw`]);
    target[`nearest_${kind}_latitude`] = safeCoordinate(source[`nearest_${kind}_latitude`], -90, 90);
    target[`nearest_${kind}_longitude`] = safeCoordinate(source[`nearest_${kind}_longitude`], -180, 180);
  }

  function safeText(value, maxLength = MAX_TEXT_LENGTH) {
    return String(value ?? "")
      .replace(/[\u0000-\u001f\u007f]/g, " ")
      .trim()
      .slice(0, maxLength);
  }

  function safeNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function safeWholeNumber(value) {
    const number = safeNumber(value);
    return number === null || number < 0 ? 0 : Math.round(number);
  }

  function safeCoordinate(value, minimum, maximum) {
    const number = safeNumber(value);
    if (number === null || number < minimum || number > maximum) return null;
    return number;
  }

  function numberFormat(value, digits = 0) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "";
    return number.toLocaleString(undefined, { maximumFractionDigits: digits });
  }

  function mwLabel(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return "";
    return `${numberFormat(number)} MW`;
  }

  function mgdLabel(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return "";
    return `${numberFormat(number, 2)} MGD`;
  }

  function moneyLabel(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return "";
    if (number >= 1_000_000_000) return `$${numberFormat(number / 1_000_000_000, 1)}B`;
    if (number >= 1_000_000) return `$${numberFormat(number / 1_000_000, 1)}M`;
    return `$${numberFormat(number)}`;
  }

  function acresLabel(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return "";
    return `${numberFormat(number, 1)} acres`;
  }

  function markerSize(site, minimum = 30, maximum = 58) {
    const capacity = Number(site.capacity_mw);
    if (Number.isFinite(capacity) && capacity > 0) {
      return clamp(Math.round(minimum + Math.sqrt(capacity) * 0.36), minimum, maximum);
    }

    const weight = Number(site.landscape_weight);
    if (Number.isFinite(weight) && weight > 0) {
      return clamp(Math.round(minimum + weight * 0.82), minimum, maximum);
    }

    return minimum;
  }

  function nuclearSize(plant) {
    const capacity = Number(plant.capacity_mw);
    if (!Number.isFinite(capacity) || capacity <= 0) return 30;
    return clamp(Math.round(28 + Math.sqrt(capacity) * 0.25), 30, 50);
  }

  function powerSize(plant) {
    const capacity = Number(plant.capacity_mw);
    if (!Number.isFinite(capacity) || capacity <= 0) return 24;
    return clamp(Math.round(22 + Math.sqrt(capacity) * 0.08), 24, 34);
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function markerHtml(size, markerClass, label, color, glow) {
    const marker = createElement("div", ["map-marker", markerClass].filter(Boolean).join(" "));
    marker.setAttribute("aria-hidden", "true");
    marker.style.setProperty("--size", `${size}px`);
    marker.style.setProperty("--marker", color);
    marker.style.setProperty("--glow", glow);
    marker.append(createElement("span", "marker-label", label));
    return marker;
  }

  function clusterIcon(count, label) {
    const size = count > 1000 ? 66 : count > 100 ? 58 : count > 20 ? 50 : 44;
    const wrapper = createElement("div", "cluster-icon");
    wrapper.style.setProperty("--size", `${size}px`);
    wrapper.append(
      createElement("b", "", numberFormat(count)),
      createElement("span", "", label)
    );
    return L.divIcon({
      html: wrapper,
      className: `cluster-wrap ${label}-cluster-wrap`,
      iconSize: L.point(size, size)
    });
  }

  function hubIcon(hub) {
    const count = Math.max(1, hub.count);
    const size = count > 25 ? 70 : count > 12 ? 62 : count > 4 ? 54 : 46;
    const wrapper = createElement("div", "hub-icon");
    wrapper.style.setProperty("--size", `${size}px`);
    wrapper.append(
      createElement("b", "", numberFormat(count)),
      createElement("span", "", "hub")
    );
    return L.divIcon({
      html: wrapper,
      className: "hub-wrap",
      iconSize: L.point(size, size),
      iconAnchor: [size / 2, size / 2],
      popupAnchor: [0, -size / 2]
    });
  }

  function hubPopup(hub) {
    const wrapper = document.createElement("div");
    wrapper.append(
      createElement("div", "popup-kicker", "Data-center hub"),
      createElement("h3", "popup-title", `${numberFormat(hub.count)} facilities`),
      popupTable([
        ["States", hub.states],
        ["Capacity", mwLabel(hub.capacity_mw)],
        ["Zoom", "Click the hub to load nearby facility markers."]
      ])
    );
    return wrapper;
  }

  function dcIcon(site) {
    const style = statusStyles[site.status_group] || statusStyles.Other;
    const size = markerSize(site);
    return L.divIcon({
      className: "dc-wrap",
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
      popupAnchor: [0, -size / 2],
      html: markerHtml(size, "", style.tag, style.color, style.glow)
    });
  }

  function nuclearIcon(plant) {
    const size = nuclearSize(plant);
    return L.divIcon({
      className: "",
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
      popupAnchor: [0, -size / 2],
      html: markerHtml(size, "nuclear-marker", "N", "#047857", "rgba(4,120,87,0.28)")
    });
  }

  function powerIcon(plant) {
    const size = powerSize(plant);
    const status = statusStyles[plant.status_group] || statusStyles.Other;
    return L.divIcon({
      className: "",
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
      popupAnchor: [0, -size / 2],
      html: markerHtml(size, "power-marker", "P", status.color, status.glow)
    });
  }

  function waterIcon() {
    const size = 28;
    return L.divIcon({
      className: "",
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
      popupAnchor: [0, -size / 2],
      html: markerHtml(size, "water-marker", "W", "#0369a1", "rgba(3,105,161,0.24)")
    });
  }

  function createElement(tagName, className, text) {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function safeExternalLink(url, label) {
    try {
      const parsed = new URL(url, window.location.href);
      if (!["http:", "https:"].includes(parsed.protocol)) {
        return document.createTextNode("");
      }

      const anchor = document.createElement("a");
      anchor.href = parsed.href;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.textContent = safeText(label || "Source", 120);
      return anchor;
    } catch {
      return document.createTextNode("");
    }
  }

  function decorateMarker(marker, label) {
    const accessibleName = safeText(label, 220);
    marker.options.alt = accessibleName;
    marker.on("add", () => {
      const element = marker.getElement();
      if (!element) return;
      element.setAttribute("role", "button");
      element.setAttribute("aria-label", accessibleName);
      element.setAttribute("aria-describedby", "mapAccessibilityText");
    });
    return marker;
  }

  function hubAccessibleLabel(hub) {
    return [
      `${numberFormat(hub.count)} data-center facilities`,
      hub.states ? `States: ${hub.states}` : "",
      mwLabel(hub.capacity_mw)
    ].filter(Boolean).join(". ");
  }

  function powerAccessibleLabel(plant, kind) {
    const label = kind === "nuclear" ? "Nuclear site" : "Power plant";
    return [
      plant.name || label,
      label,
      plant.state,
      mwLabel(plant.capacity_mw),
      plant.statuses || plant.status_group
    ].filter(Boolean).join(". ");
  }

  function waterAccessibleLabel(source) {
    return [
      source.name || "Water source reference",
      "Water source reference",
      source.type,
      source.state
    ].filter(Boolean).join(". ");
  }

  function detailNormalizer(kind) {
    if (kind === "data-center") return normalizeDataCenter;
    if (kind === "water") return normalizeWaterSource;
    return normalizePowerPlant;
  }

  async function detailShard(path) {
    if (!path) return {};
    if (!detailShardCache.has(path)) {
      detailShardCache.set(path, fetchJson(path, MAX_DETAIL_DATA_CHARS)
        .then((payload) => {
          if (!payload || typeof payload !== "object" || !payload.details || typeof payload.details !== "object") {
            throw new Error("Map detail data has an invalid shape.");
          }
          return payload.details;
        })
        .catch((error) => {
          detailShardCache.delete(path);
          throw error;
        }));
    }
    return detailShardCache.get(path);
  }

  async function loadDetailRecord(record, kind) {
    if (!record || record.detailLoaded || !record.detail_file || !record.key) return record;
    const details = await detailShard(record.detail_file);
    const detail = details[record.key];
    if (!detail || typeof detail !== "object") {
      record.detailLoaded = true;
      return record;
    }

    const normalized = detailNormalizer(kind)({
      ...detail,
      key: record.key,
      detail_file: record.detail_file,
      latitude: record.latitude,
      longitude: record.longitude
    });
    Object.assign(record, normalized, { detailLoaded: true });
    return record;
  }

  function loadingPopup(record, kind) {
    const wrapper = document.createElement("div");
    wrapper.append(
      createElement("div", "popup-kicker", detailPillLabel(record, kind)),
      createElement("h3", "popup-title", record.name || "Loading"),
      createElement("div", "detail-body", "Loading details.")
    );
    return wrapper;
  }

  function popupForKind(record, kind) {
    if (kind === "data-center") return dcPopup(record);
    if (kind === "water") return waterPopup(record);
    return powerPopup(record, kind);
  }

  function bindLazyPopup(marker, record, kind, maxWidth) {
    marker.bindPopup(loadingPopup(record, kind), { maxWidth });
    marker.on("click", () => openRecordDetails(marker, record, kind));
    return marker;
  }

  function openRecordDetails(marker, record, kind) {
    marker.setPopupContent(loadingPopup(record, kind));
    updateDetail(record, kind);
    loadDetailRecord(record, kind)
      .then((fullRecord) => {
        marker.setPopupContent(popupForKind(fullRecord, kind));
        updateDetail(fullRecord, kind);
      })
      .catch(showDataError);
  }

  function popupTable(rows) {
    const table = createElement("table", "popup-table");

    rows.forEach(([label, value]) => {
      const node = value instanceof Node ? value : document.createTextNode(safeText(value));
      if (!node.textContent.trim()) return;

      const tr = document.createElement("tr");
      const th = document.createElement("th");
      const td = document.createElement("td");

      th.textContent = safeText(label, 80);
      td.append(node);
      tr.append(th, td);
      table.append(tr);
    });

    return table;
  }

  function dcPopup(site) {
    const wrapper = document.createElement("div");

    wrapper.append(
      createElement("div", "popup-kicker", `${site.status_group} data-center facility`),
      createElement("h3", "popup-title", site.name),
      popupTable([
        ["Developer", site.developer],
        ["Status", site.status],
        ["Capacity", site.capacity_label || mwLabel(site.capacity_mw)],
        ["Location", site.location],
        ["Energy", energyLabel(site)],
        ["Water", waterUseLabel(site)],
        ["Nearest nuclear", nearestLabel(site, "nuclear", true)],
        ["Nearest power", nearestLabel(site, "power", true)],
        ["Nearest water", nearestLabel(site, "water", false)],
        ["Investment", moneyLabel(site.investment_usd)],
        ["Land", acresLabel(site.land_acres)],
        ["Notes", site.notes],
        ["Source", safeExternalLink(site.source_url, site.source || "Source")]
      ])
    );

    return wrapper;
  }

  function powerPopup(plant, kind) {
    const wrapper = document.createElement("div");
    const label = kind === "nuclear" ? "Nuclear site" : "Power plant";

    wrapper.append(
      createElement("div", "popup-kicker", label),
      createElement("h3", "popup-title", plant.name),
      popupTable([
        ["Operator", plant.operator],
        ["State", plant.state],
        ["County", plant.county],
        ["EIA/Record ID", plant.plant_id],
        ["Nameplate", mwLabel(plant.capacity_mw)],
        ["Status", plant.statuses || plant.status_group],
        ["Technology", plant.technologies],
        ["Energy source", plant.energy_sources],
        ["Notes", plant.notes],
        ["Source", safeExternalLink(plant.source_url, plant.source)]
      ])
    );

    return wrapper;
  }

  function waterPopup(source) {
    const wrapper = document.createElement("div");

    wrapper.append(
      createElement("div", "popup-kicker", "Water source reference"),
      createElement("h3", "popup-title", source.name),
      popupTable([
        ["Type", source.type],
        ["State", source.state],
        ["Notes", source.notes],
        ["Source", safeExternalLink(source.source_url, source.source)]
      ])
    );

    return wrapper;
  }

  function energyLabel(site) {
    return [
      site.powered_by,
      site.energy_source,
      site.utility,
      mwLabel(site.onsite_generation_mw)
    ].filter(Boolean).join(" / ");
  }

  function waterUseLabel(site) {
    return [
      site.water_cooling_type,
      mgdLabel(site.water_reported_mgd),
      site.water_notes
    ].filter(Boolean).join(" / ");
  }

  function nearestLabel(site, kind, includeCapacity) {
    const name = site[`nearest_${kind}_name`];
    const distance = site[`nearest_${kind}_distance_mi`];
    if (!name || !Number.isFinite(distance)) return "";

    const parts = [name];
    const state = site[`nearest_${kind}_state`];
    const type = site[`nearest_${kind}_type`];
    const source = site[`nearest_${kind}_source`];
    const capacity = site[`nearest_${kind}_capacity_mw`];

    if (state) parts.push(`(${state})`);
    if (type) parts.push(type);
    parts.push(`${numberFormat(distance, 1)} mi`);
    if (includeCapacity && Number.isFinite(capacity)) parts.push(mwLabel(capacity));
    if (source) parts.push(source);
    return parts.filter(Boolean).join(" ");
  }

  function updateDetail(site, kind) {
    if (!site) return;

    const title = createElement("div", "detail-title");
    const heading = createElement("h2", "", site.name);
    const pill = createElement("span", "pill", detailPillLabel(site, kind));
    const details = createElement("dl", "detail-list");

    if (kind === "data-center") {
      const style = statusStyles[site.status_group] || statusStyles.Other;
      pill.style.background = style.color;
      appendDetailRows(details, [
        ["Developer", site.developer],
        ["Capacity", site.capacity_label || mwLabel(site.capacity_mw)],
        ["Energy", energyLabel(site)],
        ["Water", waterUseLabel(site)],
        ["Nearest N", nearestLabel(site, "nuclear", true)],
        ["Nearest P", nearestLabel(site, "power", true)],
        ["Nearest W", nearestLabel(site, "water", false)]
      ]);
      title.append(heading, pill);
      detailPanel.replaceChildren(
        title,
        createElement("div", "detail-body", site.notes || site.location),
        details
      );
      return;
    }

    if (kind === "water") {
      pill.style.background = "#0ea5e9";
      appendDetailRows(details, [
        ["Type", site.type],
        ["State", site.state],
        ["Notes", site.notes],
        ["Source", site.source]
      ]);
      title.append(heading, pill);
      detailPanel.replaceChildren(title, details);
      return;
    }

    pill.style.background = kind === "nuclear" ? "#15803d" : "#ea580c";
    appendDetailRows(details, [
      ["Operator", site.operator],
      ["State", site.state],
      ["County", site.county],
      ["Nameplate", mwLabel(site.capacity_mw)],
      ["Status", site.statuses || site.status_group],
      ["Energy", site.energy_sources]
    ]);
    title.append(heading, pill);
    detailPanel.replaceChildren(title, details);
  }

  function detailPillLabel(site, kind) {
    if (kind === "data-center") return site.status_group;
    if (kind === "water") return "Water";
    if (kind === "nuclear") return "Nuclear";
    return "Power";
  }

  function appendDetailRows(list, rows) {
    rows.forEach(([label, value]) => {
      const text = safeText(value);
      if (!text) return;
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = text;
      list.append(dt, dd);
    });
  }

  function buildHeatPoints() {
    if (heatPoints) return heatPoints;

    const dcPoints = dataCenters.map((site) => {
      const capacity = Number(site.capacity_mw);
      const weight = Number(site.landscape_weight);
      const intensity = Number.isFinite(capacity) && capacity > 0
        ? Math.min(1, Math.log10(capacity + 10) / 4.1)
        : Math.min(0.78, Math.max(0.32, (weight || 0) / 36));
      return [site.latitude, site.longitude, intensity];
    });

    const nuclearPoints = nuclearPlants.map((plant) => {
      const capacity = Number(plant.capacity_mw);
      const intensity = Number.isFinite(capacity) && capacity > 0
        ? Math.min(0.85, Math.log10(capacity + 10) / 4.4)
        : 0.45;
      return [plant.latitude, plant.longitude, intensity];
    });

    heatPoints = dcPoints.concat(nuclearPoints);
    return heatPoints;
  }

  function heatOptionsForZoom() {
    const zoom = clamp(map.getZoom(), HEAT_BASE_ZOOM, VISIBILITY.heatMaxZoom);
    const scale = Math.pow(HEAT_RADIUS_SCALE, zoom - HEAT_BASE_ZOOM);
    return {
      radius: Math.round(clamp(HEAT_BASE_RADIUS * scale, 30, 54)),
      blur: Math.round(clamp(HEAT_BASE_BLUR * scale, 24, 46)),
      maxZoom: VISIBILITY.heatMaxZoom,
      gradient: HEAT_GRADIENT
    };
  }

  function getHeatLayer() {
    if (heatLayer) return heatLayer;

    heatLayer = L.heatLayer(buildHeatPoints(), heatOptionsForZoom());

    return heatLayer;
  }

  function scheduleHeatLayerRefresh() {
    if (!heatLayer) return;
    if (heatRefreshTimer) window.clearTimeout(heatRefreshTimer);
    heatRefreshTimer = window.setTimeout(() => {
      heatRefreshTimer = 0;
      updateHeatLayerForZoom();
    }, HEAT_REFRESH_DELAY_MS);
  }

  function updateHeatLayerForZoom() {
    if (!heatLayer) return;
    const options = heatOptionsForZoom();
    if (typeof heatLayer.setOptions === "function") {
      heatLayer.setOptions(options);
    } else {
      heatLayer.options = { ...heatLayer.options, ...options };
    }
    if (typeof heatLayer.redraw === "function") {
      heatLayer.redraw();
    }
  }

  function activeStatuses() {
    return new Set(
      Array.from(document.querySelectorAll("#statusFilters input:checked")).map((input) => input.value)
    );
  }

  function layerVisibility() {
    const zoom = map.getZoom();
    return {
      dataHubs: zoom <= VISIBILITY.dataHubMaxZoom,
      dataMarkers: zoom >= VISIBILITY.dataMarkerMinZoom,
      connectors: showConnectors.checked && zoom >= VISIBILITY.connectorMinZoom,
      nuclear: showNuclear.checked && zoom >= VISIBILITY.nuclearMinZoom,
      water: showWater.checked && zoom >= VISIBILITY.waterMinZoom,
      power: showPower.checked && zoom >= VISIBILITY.powerMinZoom,
      heat: showHeat.checked && zoom <= VISIBILITY.heatMaxZoom
    };
  }

  function viewportKey(digits = 1) {
    const bounds = map.getBounds();
    const round = (value) => Number(value).toFixed(digits);
    return [
      map.getZoom(),
      round(bounds.getSouth()),
      round(bounds.getWest()),
      round(bounds.getNorth()),
      round(bounds.getEast())
    ].join(":");
  }

  function dataCenterLayerKey(statuses, query, visibility) {
    const dataBubbleLayers = effectiveDataBubbleLayers(visibility);
    return [
      Array.from(statuses).sort().join(","),
      query,
      dataBubbleLayers.hubs ? "hubs" : "no-hubs",
      dataBubbleLayers.markers ? "markers" : "no-markers",
      visibility.connectors && dataBubbleLayers.markers ? "connectors" : "no-connectors"
    ].join("|");
  }

  function effectiveDataBubbleLayers(visibility) {
    const overlap = Boolean(dataBubbleTransitionMode);
    return {
      hubs: visibility.dataHubs || overlap,
      markers: visibility.dataMarkers || overlap
    };
  }

  function recordsInMapBounds(records, padding) {
    const bounds = map.getBounds().pad(padding);
    return records.filter((record) => bounds.contains([record.latitude, record.longitude]));
  }

  function tileIntersectsBounds(tile, bounds) {
    const [south, west, north, east] = tile.bounds;
    return (
      south <= bounds.getNorth()
      && north >= bounds.getSouth()
      && west <= bounds.getEast()
      && east >= bounds.getWest()
    );
  }

  function powerTilesForViewport(tiles) {
    const bounds = map.getBounds().pad(POWER_BOUNDS_PADDING);
    return tiles.filter((tile) => tileIntersectsBounds(tile, bounds));
  }

  function siteMatchesSearch(site, query) {
    return !query || site.searchText.includes(query);
  }

  function filteredDataCenters(statuses, query) {
    return dataCenters.filter((site) => statuses.has(site.status_group) && siteMatchesSearch(site, query));
  }

  function hubCacheKey(sites) {
    return sites.map((site) => site.key).join("|");
  }

  function hubsForSites(sites) {
    const fullUnfilteredSet = sites.length === dataCenters.length
      && sites.every((site, index) => site === dataCenters[index]);
    if (fullUnfilteredSet && dataCenterHubs.length) return dataCenterHubs;
    const cacheKey = hubCacheKey(sites);
    if (hubBuildCache.has(cacheKey)) return hubBuildCache.get(cacheKey);

    const gridSize = 0.85;
    const groups = new Map();
    sites.forEach((site) => {
      const key = `${Math.floor(site.latitude / gridSize)}:${Math.floor(site.longitude / gridSize)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(site);
    });

    const hubs = Array.from(groups.entries()).map(([key, group]) => {
      const count = group.length;
      const latitude = group.reduce((total, site) => total + site.latitude, 0) / count;
      const longitude = group.reduce((total, site) => total + site.longitude, 0) / count;
      const capacity = group.reduce((total, site) => total + (safeNumber(site.capacity_mw) || 0), 0);
      const states = Array.from(new Set(group.map((site) => site.state).filter(Boolean))).slice(0, 4).join(", ");
      return {
        key,
        name: `${count} facilities`,
        latitude,
        longitude,
        count,
        capacity_mw: capacity,
        states
      };
    });
    if (hubBuildCache.size > 24) {
      hubBuildCache.clear();
    }
    hubBuildCache.set(cacheKey, hubs);
    return hubs;
  }

  function scheduleDataCenterRefresh() {
    if (refreshFrame) cancelAnimationFrame(refreshFrame);
    refreshFrame = requestAnimationFrame(() => {
      refreshFrame = 0;
      refreshDataCenterLayer();
    });
  }

  function refreshDataCenterLayer(options = {}) {
    const shouldRenderResults = options.renderResults !== false;
    const statuses = activeStatuses();
    const query = searchInput.value.trim().toLowerCase();
    const visibility = layerVisibility();
    const dataBubbleLayers = effectiveDataBubbleLayers(visibility);
    const filtered = filteredDataCenters(statuses, query);
    const renderKey = dataCenterLayerKey(statuses, query, visibility);

    shownCount.textContent = filtered.length.toLocaleString();
    if (shouldRenderResults) renderResultsList(filtered);
    if (renderKey === dataCenterRenderKey) return;
    dataCenterRenderKey = renderKey;

    if (dataBubbleLayers.hubs) {
      dcHubLayer.clearLayers();
      const hubs = hubsForSites(filtered);
      const hubMarkers = hubs.map((hub) => getHubMarker(hub));
      hubMarkers.forEach((marker) => dcHubLayer.addLayer(marker));
    }

    if (dataBubbleLayers.markers) {
      dcCluster.clearLayers();
      const markers = filtered.map((site) => getDataCenterMarker(site));
      dcCluster.addLayers(markers);

      if (visibility.connectors) {
        connectorLayer.clearLayers();
        filtered.forEach((site) => {
          addProximityLine(site, "nuclear");
          addProximityLine(site, "power");
          addProximityLine(site, "water");
        });
      }
    }

    if (!visibility.connectors || !dataBubbleLayers.markers) {
      connectorLayer.clearLayers();
    }
    if (!dataBubbleLayers.hubs) {
      dcHubLayer.clearLayers();
    }
    if (!dataBubbleLayers.markers) {
      dcCluster.clearLayers();
    }
  }

  function getHubMarker(hub) {
    if (hubMarkerCache.has(hub.key)) return hubMarkerCache.get(hub.key);
    const marker = decorateMarker(L.marker([hub.latitude, hub.longitude], {
      icon: hubIcon(hub),
      title: `${numberFormat(hub.count)} data-center facilities`,
      keyboard: true
    })
      .bindPopup(hubPopup(hub), { maxWidth: 320 })
      .bindTooltip(`${numberFormat(hub.count)} facilities`), hubAccessibleLabel(hub));
    marker.on("click", () => {
      const targetZoom = Math.max(VISIBILITY.dataMarkerMinZoom, map.getZoom() + 2);
      moveToFeature([hub.latitude, hub.longitude], targetZoom, () => {
        refreshDataCenterLayer({ renderResults: false });
        syncLayerToggles();
      });
    });
    hubMarkerCache.set(hub.key, marker);
    return marker;
  }

  function getDataCenterMarker(site) {
    if (dataCenterMarkerCache.has(site.key)) return dataCenterMarkerCache.get(site.key);
    const marker = decorateMarker(L.marker([site.latitude, site.longitude], {
      icon: dcIcon(site),
      title: site.name,
      keyboard: true
    })
      .bindTooltip(`${site.name} - ${site.status_group}`), dataCenterAccessibleLabel(site));

    bindLazyPopup(marker, site, "data-center", 380);
    dataCenterMarkerCache.set(site.key, marker);
    return marker;
  }

  function addProximityLine(site, kind) {
    const latitude = site[`nearest_${kind}_latitude`];
    const longitude = site[`nearest_${kind}_longitude`];
    if (latitude === null || longitude === null) return;

    const style = proximityStyles[kind];
    const key = `${site.key}:${kind}`;
    if (!dataCenterLineCache.has(key)) {
      dataCenterLineCache.set(key, L.polyline(
      [
        [site.latitude, site.longitude],
        [latitude, longitude]
      ],
      {
        color: style.color,
        weight: kind === "water" ? 2 : 2.4,
        opacity: style.opacity,
        dashArray: style.dashArray,
        renderer: connectorRenderer,
        interactive: false
      }
      ));
    }
    connectorLayer.addLayer(dataCenterLineCache.get(key));
  }

  function buildNuclearLayer() {
    nuclearLayer.clearLayers();
    nuclearPlants.forEach((plant) => {
      const marker = decorateMarker(L.marker([plant.latitude, plant.longitude], {
        icon: nuclearIcon(plant),
        title: plant.name,
        keyboard: true
      })
        .bindTooltip(`${plant.name} - ${mwLabel(plant.capacity_mw)}`), powerAccessibleLabel(plant, "nuclear"));

      bindLazyPopup(marker, plant, "nuclear", 360);
      nuclearLayer.addLayer(marker);
    });
  }

  function buildWaterLayer() {
    waterLayer.clearLayers();
    waterSources.forEach((source) => {
      const marker = decorateMarker(L.marker([source.latitude, source.longitude], {
        icon: waterIcon(source),
        title: source.name,
        keyboard: true
      })
        .bindTooltip(`${source.name} - ${source.type}`), waterAccessibleLabel(source));

      bindLazyPopup(marker, source, "water", 340);
      waterLayer.addLayer(marker);
    });
  }

  async function getPowerTileIndex() {
    if (powerTileIndex) return powerTileIndex;
    if (!powerTileIndexRequest) {
      powerTileIndexRequest = fetchJson(DATA_ENDPOINTS.powerTileIndex, MAX_POWER_TILE_INDEX_CHARS)
        .then(normalizePowerTileIndex)
        .catch((error) => {
          powerTileIndexRequest = null;
          throw error;
        });
    }
    powerTileIndex = await powerTileIndexRequest;
    return powerTileIndex;
  }

  function powerLayerRequestKey(tiles) {
    return `${viewportKey(2)}|${tiles.map((tile) => tile.file).join(",")}`;
  }

  async function getPowerTileRecords(tile) {
    if (!powerTileCache.has(tile.file)) {
      powerTileCache.set(tile.file, fetchJson(tile.file, MAX_POWER_TILE_CHARS)
        .then((payload) => normalizeGeoJsonCollection(payload, normalizePowerPlant, MAX_POWER_RECORDS))
        .catch((error) => {
          powerTileCache.delete(tile.file);
          throw error;
        }));
    }
    return powerTileCache.get(tile.file);
  }

  async function getPowerPlantsForViewport() {
    const tiles = powerTilesForViewport(await getPowerTileIndex());
    const requestKey = powerLayerRequestKey(tiles);
    powerRequestKey = requestKey;
    const groups = await Promise.all(tiles.map(getPowerTileRecords));
    return {
      requestKey,
      plants: groups.flat()
    };
  }

  function ensurePowerCluster() {
    if (powerCluster) return powerCluster;
    powerCluster = L.markerClusterGroup({
      showCoverageOnHover: false,
      spiderfyOnMaxZoom: false,
      removeOutsideVisibleBounds: false,
      animate: false,
      animateAddingMarkers: false,
      maxClusterRadius: 38,
      chunkedLoading: true,
      chunkInterval: 100,
      chunkDelay: 45,
      iconCreateFunction: (cluster) => clusterIcon(cluster.getChildCount(), "power")
    });

    return powerCluster;
  }

  function getPowerMarker(plant) {
    if (powerMarkerCache.has(plant.key)) return powerMarkerCache.get(plant.key);
    const marker = decorateMarker(L.marker([plant.latitude, plant.longitude], {
      icon: powerIcon(plant),
      title: plant.name,
      keyboard: true
    })
      .bindTooltip(`${plant.name} - ${mwLabel(plant.capacity_mw)}`), powerAccessibleLabel(plant, "power"));

    bindLazyPopup(marker, plant, "power", 360);
    powerMarkerCache.set(plant.key, marker);
    return marker;
  }

  function schedulePowerLayerRefresh() {
    if (isZooming) return;
    if (powerRefreshTimer) window.clearTimeout(powerRefreshTimer);
    if (powerRefreshFrame) cancelAnimationFrame(powerRefreshFrame);
    powerRefreshTimer = window.setTimeout(() => {
      powerRefreshTimer = 0;
      if (isZooming) return;
      powerRefreshFrame = requestAnimationFrame(() => {
        powerRefreshFrame = 0;
        refreshPowerLayer().catch(showDataError);
      });
    }, POWER_REFRESH_DELAY_MS);
  }

  function cancelPowerLayerRefresh() {
    if (powerRefreshTimer) {
      window.clearTimeout(powerRefreshTimer);
      powerRefreshTimer = 0;
    }
    if (powerRefreshFrame) {
      cancelAnimationFrame(powerRefreshFrame);
      powerRefreshFrame = 0;
    }
  }

  async function refreshPowerLayer() {
    const visibility = layerVisibility();
    if (!visibility.power) {
      if (powerCluster) toggleMapLayer(powerCluster, false);
      powerRenderKey = "";
      return;
    }

    const cluster = ensurePowerCluster();
    toggleMapLayer(cluster, true);

    const { requestKey, plants } = await getPowerPlantsForViewport();
    if (requestKey !== powerRequestKey || !layerVisibility().power) return;

    const visiblePlants = recordsInMapBounds(plants, POWER_BOUNDS_PADDING)
      .slice(0, MAX_RENDERED_POWER_MARKERS);
    const renderKey = `${requestKey}|${visiblePlants.map((plant) => plant.key).join(",")}`;
    if (renderKey === powerRenderKey) return;

    powerRenderKey = renderKey;
    cluster.clearLayers();
    cluster.addLayers(visiblePlants.map((plant) => getPowerMarker(plant)));
  }

  function syncLayerToggles() {
    const visibility = layerVisibility();
    const dataBubbleLayers = effectiveDataBubbleLayers(visibility);
    toggleMapLayer(dcHubLayer, dataBubbleLayers.hubs);
    toggleMapLayer(dcCluster, dataBubbleLayers.markers);
    toggleMapLayer(connectorLayer, visibility.connectors && dataBubbleLayers.markers);
    toggleMapLayer(nuclearLayer, visibility.nuclear);
    toggleMapLayer(waterLayer, visibility.water);

    if (visibility.power) {
      schedulePowerLayerRefresh();
    } else if (powerCluster) {
      cancelPowerLayerRefresh();
      toggleMapLayer(powerCluster, false);
      powerRenderKey = "";
    }

    if (visibility.heat) {
      const layer = getHeatLayer();
      updateHeatLayerForZoom();
      toggleMapLayer(layer, true);
    } else if (heatLayer) {
      toggleMapLayer(heatLayer, false);
    }

    [showNuclear, showPower, showWater, showConnectors, showHeat].forEach((input) => {
      input.closest(".layer-toggle").classList.toggle("active", input.checked);
    });
  }

  function syncStatusChipState() {
    document.querySelectorAll("#statusFilters .chip").forEach((chip) => {
      const input = chip.querySelector("input");
      chip.classList.toggle("active", Boolean(input && input.checked));
    });
  }

  function toggleMapLayer(layer, shouldShow) {
    if (shouldShow && !map.hasLayer(layer)) {
      map.addLayer(layer);
    } else if (!shouldShow && map.hasLayer(layer)) {
      map.removeLayer(layer);
    }
  }

  function fitToData() {
    const bounds = L.latLngBounds([]);
    dataCenters.forEach((site) => bounds.extend([site.latitude, site.longitude]));
    nuclearPlants.forEach((plant) => bounds.extend([plant.latitude, plant.longitude]));
    waterSources.forEach((source) => bounds.extend([source.latitude, source.longitude]));

    if (bounds.isValid()) {
      map.fitBounds(bounds.pad(0.08), { maxZoom: 5 });
    }

    map.setMaxBounds(US_BOUNDS);
  }
}());
