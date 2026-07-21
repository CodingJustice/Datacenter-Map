# Datacenter Map

Interactive U.S. map of public data-center facilities, nuclear sites, nearby
power plants, and named water-source reference points.

Market-hub placeholders are no longer included. The default data-center layer
comes from public facility records.

## Files

- `us_ai_datacenters_nuclear_map.html` defines the page structure, security
  policy, controls, and script loading order.
- `styles.css` controls map, marker, popup, and responsive layout styling.
- `app.js` powers the browser map, filters, popups, lazy power-plant layer,
  heatmap, and proximity lines.
- `map-data.js` is the generated browser data file.
- `script.py` refreshes public source data and writes CSV exports plus
  `map-data.js`.
- `MapGenerator.java` rebuilds `map-data.js` from the CSV exports using Java.
- `water_sources.csv` contains the named water-reference points used for
  proximity calculations.

## Current Data

- 422 public data-center facility records from Compute Atlas.
- 15,975 operating and planned power-plant records from EIA-860M plus Compute
  Atlas power-generation project records.
- 63 nuclear operating, planned, restart, or SMR-related records.
- 41 named water-source reference points for proximity context.

Sources:

- Compute Atlas API: https://www.compute-atlas.com/api
- EIA-860M: https://www.eia.gov/electricity/data/eia860m/index.php
- USGS National Hydrography Dataset: https://www.usgs.gov/national-hydrography/national-hydrography-dataset
- Natural Earth: https://www.naturalearthdata.com/

## Run The Python Version

Refresh source data, rebuild CSVs, and rewrite the browser data file:

```powershell
python script.py
```

To request fresh Compute Atlas JSON and EIA data where network access is
available:

```powershell
python script.py --refresh-compute-atlas --refresh-eia
```

Useful options:

```powershell
python script.py --compute-atlas-json compute-atlas-data-centers.json --water-sources water_sources.csv --output-js map-data.js
```

## Run The Java Version

Rebuild `map-data.js` from the CSV exports:

```powershell
javac MapGenerator.java
java MapGenerator
```

Useful options:

```powershell
java MapGenerator --data-centers us_ai_datacenters_nuclear_map_data_centers.csv --power-plants us_ai_datacenters_nuclear_map_power_plants.csv --nuclear-plants us_ai_datacenters_nuclear_map_nuclear_plants.csv --water-sources us_ai_datacenters_nuclear_map_water_sources.csv --output-js map-data.js
```

Then open `us_ai_datacenters_nuclear_map.html` in a browser.

## Security And Performance

- A content security policy limits scripts, images, forms, frames, and objects.
- Marker and popup text is normalized and rendered through DOM APIs to reduce
  cross-site scripting risk.
- External links are limited to `http` and `https` and use
  `noopener noreferrer`.
- Scripts load with `defer`, and `map-data.js` is a cacheable external file.
- The 15,975-record power-plant layer is off by default and built only when the
  Power toggle is enabled.
- Marker clustering uses chunked loading to reduce browser pauses.
- Search/filter refreshes are scheduled with `requestAnimationFrame`.
- Marker animation is minimized so large overlays do not waste CPU.

## Data Notes

The map uses public, source-cited records. It is not a proprietary commercial
facility registry, and exact locations depend on what each public source
discloses. Water proximity uses named surface-water reference points and any
reported water/cooling notes in the data-center records; it is not a legal
water-rights or utility-service determination.
