(function () {
  "use strict";

  /*
   * Main browser behavior for the infrastructure map.
   *
   * The HTML file owns page structure, map-data.js owns generated data, and
   * this file owns safe rendering, filtering, layer toggles, and popups.
   * Large optional layers are lazy-built so the first map view stays responsive.
   */

  const MAX_TEXT_LENGTH = 500;
  const MAX_DATA_CENTER_RECORDS = 2000;
  const MAX_POWER_RECORDS = 25000;
  const MAX_WATER_RECORDS = 1000;

  const rawData = window.DatacenterMapData || {};

  const statusStyles = Object.freeze({
    Operating: { color: "#00a6ff", glow: "rgba(0, 166, 255, 0.26)", tag: "OP" },
    "Under construction": { color: "#ffb000", glow: "rgba(255, 176, 0, 0.28)", tag: "UC" },
    Planned: { color: "#ff3d7f", glow: "rgba(255, 61, 127, 0.26)", tag: "PL" },
    Proposed: { color: "#8b5cf6", glow: "rgba(139, 92, 246, 0.27)", tag: "PR" },
    Permitted: { color: "#14b8a6", glow: "rgba(20, 184, 166, 0.22)", tag: "PM" },
    Cancelled: { color: "#64748b", glow: "rgba(100, 116, 139, 0.22)", tag: "CA" },
    Other: { color: "#334155", glow: "rgba(51, 65, 85, 0.2)", tag: "DC" }
  });

  const proximityStyles = Object.freeze({
    nuclear: { color: "#21c55d", dashArray: "4 7", opacity: 0.36 },
    power: { color: "#f97316", dashArray: "2 7", opacity: 0.3 },
    water: { color: "#0ea5e9", dashArray: "1 7", opacity: 0.32 }
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

  // Normalize small/default layers immediately; keep power raw until requested.
  const dataCenters = normalizeCollection(rawData.dataCenters, normalizeDataCenter, MAX_DATA_CENTER_RECORDS);
  const nuclearPlants = normalizeCollection(rawData.nuclearPlants, normalizePowerPlant, MAX_WATER_RECORDS);
  const waterSources = normalizeCollection(rawData.waterSources, normalizeWaterSource, MAX_WATER_RECORDS);
  const rawPowerPlants = Array.isArray(rawData.powerPlants)
    ? rawData.powerPlants.slice(0, MAX_POWER_RECORDS)
    : [];

  const map = L.map("map", {
    maxBounds: US_BOUNDS,
    maxBoundsViscosity: 1.0,
    minZoom: 4,
    maxZoom: 10,
    zoomControl: false,
    preferCanvas: true,
    scrollWheelZoom: true
  });

  map.fitBounds(US_BOUNDS, { padding: [20, 20] });

  // Tile layers are external but version-stable; the CSP only allows vetted hosts.
  const baseLayers = {
    Color: L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
      maxZoom: 19,
      updateWhenIdle: true,
      keepBuffer: 2,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
    }),
    Light: L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      maxZoom: 19,
      updateWhenIdle: true,
      keepBuffer: 2,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
    }),
    Dark: L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      maxZoom: 19,
      updateWhenIdle: true,
      keepBuffer: 2,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
    }),
    Satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 19,
      updateWhenIdle: true,
      keepBuffer: 2,
      attribution: "Tiles &copy; Esri"
    })
  };

  const dcCluster = L.markerClusterGroup({
    showCoverageOnHover: false,
    spiderfyOnMaxZoom: true,
    maxClusterRadius: 44,
    chunkedLoading: true,
    chunkInterval: 120,
    chunkDelay: 35,
    iconCreateFunction: (cluster) => clusterIcon(cluster.getChildCount(), "sites")
  });

  const nuclearLayer = L.layerGroup();
  const waterLayer = L.layerGroup();
  const connectorLayer = L.layerGroup();

  let powerPlants = null;
  let powerCluster = null;
  let heatLayer = null;
  let refreshFrame = 0;

  baseLayers.Color.addTo(map);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  L.control.layers(baseLayers, null, { position: "bottomright", collapsed: true }).addTo(map);

  map.addLayer(dcCluster);
  map.addLayer(nuclearLayer);
  map.addLayer(waterLayer);
  map.addLayer(connectorLayer);

  buildNuclearLayer();
  buildWaterLayer();
  refreshDataCenterLayer();
  syncLayerToggles();
  fitToData();
  bindControls();

  if (rawData.generatedAt) {
    map.attributionControl.addAttribution(`Generated ${safeText(rawData.generatedAt)}`);
  }

  function bindControls() {
    searchInput.addEventListener("input", scheduleDataCenterRefresh);

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

  function normalizeCollection(collection, normalizer, maxRecords) {
    if (!Array.isArray(collection)) return [];
    return collection
      .slice(0, maxRecords)
      .map(normalizer)
      .filter((item) => item.latitude !== null && item.longitude !== null);
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

    normalized.searchText = [
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
      name: safeText(item.name),
      latitude: safeCoordinate(item.latitude, -90, 90),
      longitude: safeCoordinate(item.longitude, -180, 180),
      source: safeText(item.source),
      source_url: safeText(item.source_url),
      notes: safeText(item.notes)
    };
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

  function markerSize(site, minimum = 24, maximum = 54) {
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
    if (!Number.isFinite(capacity) || capacity <= 0) return 24;
    return clamp(Math.round(23 + Math.sqrt(capacity) * 0.28), 24, 46);
  }

  function powerSize(plant) {
    const capacity = Number(plant.capacity_mw);
    if (!Number.isFinite(capacity) || capacity <= 0) return 14;
    return clamp(Math.round(13 + Math.sqrt(capacity) * 0.09), 14, 30);
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function markerHtml(size, markerClass, label, color, glow) {
    return `<div class="map-marker ${markerClass}" style="--size:${size}px;--marker:${color};--glow:${glow}"><span class="marker-label">${label}</span></div>`;
  }

  function clusterIcon(count, label) {
    const size = count > 1000 ? 66 : count > 100 ? 58 : count > 20 ? 50 : 44;
    return L.divIcon({
      html: `<div class="cluster-icon" style="--size:${size}px"><b>${count.toLocaleString()}</b><span>${label}</span></div>`,
      className: "cluster-wrap",
      iconSize: L.point(size, size)
    });
  }

  function dcIcon(site) {
    const style = statusStyles[site.status_group] || statusStyles.Other;
    const size = markerSize(site);
    return L.divIcon({
      className: "",
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
      html: markerHtml(size, "nuclear-marker", "N", "#21c55d", "rgba(33,197,93,0.28)")
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
    const size = 22;
    return L.divIcon({
      className: "",
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
      popupAnchor: [0, -size / 2],
      html: markerHtml(size, "water-marker", "W", "#0ea5e9", "rgba(14,165,233,0.24)")
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

    return dcPoints.concat(nuclearPoints);
  }

  function getHeatLayer() {
    if (heatLayer) return heatLayer;

    heatLayer = L.heatLayer(buildHeatPoints(), {
      radius: 34,
      blur: 30,
      maxZoom: 7,
      gradient: {
        0.18: "#22d3ee",
        0.38: "#22c55e",
        0.58: "#facc15",
        0.78: "#fb7185",
        1.0: "#a855f7"
      }
    });

    return heatLayer;
  }

  function activeStatuses() {
    return new Set(
      Array.from(document.querySelectorAll("#statusFilters input:checked")).map((input) => input.value)
    );
  }

  function siteMatchesSearch(site, query) {
    return !query || site.searchText.includes(query);
  }

  function scheduleDataCenterRefresh() {
    if (refreshFrame) cancelAnimationFrame(refreshFrame);
    refreshFrame = requestAnimationFrame(() => {
      refreshFrame = 0;
      refreshDataCenterLayer();
    });
  }

  function refreshDataCenterLayer() {
    const statuses = activeStatuses();
    const query = searchInput.value.trim().toLowerCase();

    dcCluster.clearLayers();
    connectorLayer.clearLayers();

    let shown = 0;
    const markers = [];

    dataCenters.forEach((site) => {
      if (!statuses.has(site.status_group) || !siteMatchesSearch(site, query)) return;

      shown += 1;
      const marker = L.marker([site.latitude, site.longitude], {
        icon: dcIcon(site),
        title: site.name,
        keyboard: true
      })
        .bindPopup(dcPopup(site), { maxWidth: 380 })
        .bindTooltip(`${site.name} - ${site.status_group}`);

      marker.on("click", () => updateDetail(site, "data-center"));
      markers.push(marker);

      if (showConnectors.checked) {
        addProximityLine(site, "nuclear");
        addProximityLine(site, "power");
        addProximityLine(site, "water");
      }
    });

    dcCluster.addLayers(markers);
    shownCount.textContent = shown.toLocaleString();
  }

  function addProximityLine(site, kind) {
    const latitude = site[`nearest_${kind}_latitude`];
    const longitude = site[`nearest_${kind}_longitude`];
    if (latitude === null || longitude === null) return;

    const style = proximityStyles[kind];
    connectorLayer.addLayer(L.polyline(
      [
        [site.latitude, site.longitude],
        [latitude, longitude]
      ],
      {
        color: style.color,
        weight: kind === "water" ? 1.1 : 1.3,
        opacity: style.opacity,
        dashArray: style.dashArray,
        interactive: false
      }
    ));
  }

  function buildNuclearLayer() {
    nuclearLayer.clearLayers();
    nuclearPlants.forEach((plant) => {
      const marker = L.marker([plant.latitude, plant.longitude], {
        icon: nuclearIcon(plant),
        title: plant.name,
        keyboard: true
      })
        .bindPopup(powerPopup(plant, "nuclear"), { maxWidth: 360 })
        .bindTooltip(`${plant.name} - ${mwLabel(plant.capacity_mw)}`);

      marker.on("click", () => updateDetail(plant, "nuclear"));
      nuclearLayer.addLayer(marker);
    });
  }

  function buildWaterLayer() {
    waterLayer.clearLayers();
    waterSources.forEach((source) => {
      const marker = L.marker([source.latitude, source.longitude], {
        icon: waterIcon(source),
        title: source.name,
        keyboard: true
      })
        .bindPopup(waterPopup(source), { maxWidth: 340 })
        .bindTooltip(`${source.name} - ${source.type}`);

      marker.on("click", () => updateDetail(source, "water"));
      waterLayer.addLayer(marker);
    });
  }

  function getPowerPlants() {
    if (powerPlants) return powerPlants;
    powerPlants = normalizeCollection(rawPowerPlants, normalizePowerPlant, MAX_POWER_RECORDS);
    return powerPlants;
  }

  function getPowerCluster() {
    if (powerCluster) return powerCluster;

    powerCluster = L.markerClusterGroup({
      showCoverageOnHover: false,
      spiderfyOnMaxZoom: false,
      maxClusterRadius: 38,
      chunkedLoading: true,
      chunkInterval: 100,
      chunkDelay: 45,
      iconCreateFunction: (cluster) => clusterIcon(cluster.getChildCount(), "power")
    });

    const markers = getPowerPlants().map((plant) => {
      const marker = L.marker([plant.latitude, plant.longitude], {
        icon: powerIcon(plant),
        title: plant.name,
        keyboard: true
      })
        .bindPopup(powerPopup(plant, "power"), { maxWidth: 360 })
        .bindTooltip(`${plant.name} - ${mwLabel(plant.capacity_mw)}`);

      marker.on("click", () => updateDetail(plant, "power"));
      return marker;
    });

    powerCluster.addLayers(markers);
    return powerCluster;
  }

  function syncLayerToggles() {
    toggleMapLayer(nuclearLayer, showNuclear.checked);
    toggleMapLayer(waterLayer, showWater.checked);
    toggleMapLayer(connectorLayer, showConnectors.checked);

    if (showPower.checked) {
      toggleMapLayer(getPowerCluster(), true);
    } else if (powerCluster) {
      toggleMapLayer(powerCluster, false);
    }

    if (showHeat.checked) {
      toggleMapLayer(getHeatLayer(), true);
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
