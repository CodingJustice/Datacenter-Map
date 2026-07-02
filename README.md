# Datacenter Map

Generates an interactive U.S. Leaflet map of public data-center activity,
major data-center markets, and operating nuclear power plants.

## Run

```powershell
python script.py
```

The script uses only the Python standard library. It reads the latest EIA-860M
workbook when available and falls back to the newest cached workbook in
`cache/`.

## Outputs

- `us_ai_datacenters_nuclear_map.html`
- `us_ai_datacenters_nuclear_map_nuclear_plants.csv`
- `us_ai_datacenters_nuclear_map_data_centers.csv`
- `us_ai_datacenters_nuclear_map_ai_datacenters.csv` for compatibility with
  the original output name

The default data-center layer is a curated public landscape layer, not a full
facility census. Use `--datacenters-csv` to provide a custom list with at least
`name`, `latitude`, and `longitude`.
