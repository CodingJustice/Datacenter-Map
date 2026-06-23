"""
Build an interactive U.S. map of public AI/data-center projects alongside
nuclear power plants.

Nuclear plant data is pulled from EIA-860M, the official monthly generator
inventory. The default AI/data-center layer is a curated public list of major
AI-scale and hyperscale projects; pass --datacenters-csv to use your own full
facility list.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import folium
import geopandas as gpd
import pandas as pd
import requests
import urllib3
from folium.plugins import Fullscreen, MiniMap


EIA_860M_PAGE = "https://www.eia.gov/electricity/data/eia860m/"


DEFAULT_AI_DATACENTERS = [
    {
        "name": "Fairwater 1 - Mount Pleasant",
        "developer": "Microsoft",
        "status": "Operating",
        "capacity_mw": 400,
        "location": "Mount Pleasant / Racine County, Wisconsin",
        "latitude": 42.7261,
        "longitude": -87.7829,
        "source": "Cleanview public tracker; Microsoft Fairwater announcement",
        "source_url": "https://cleanview.co/data-centers/us",
        "notes": "AI datacenter megafactory; public location is approximate.",
    },
    {
        "name": "Project Zodiac - Phase 1",
        "developer": "Google",
        "status": "Operating",
        "capacity_mw": 400,
        "location": "Allen County, Indiana",
        "latitude": 41.0793,
        "longitude": -85.1394,
        "source": "Cleanview public tracker",
        "source_url": "https://cleanview.co/data-centers/us",
        "notes": "County-level public location.",
    },
    {
        "name": "Fairwater 2 - Atlanta",
        "developer": "Microsoft",
        "status": "Operating",
        "capacity_mw": 350,
        "location": "Fulton County, Georgia",
        "latitude": 33.7490,
        "longitude": -84.3880,
        "source": "Cleanview public tracker",
        "source_url": "https://cleanview.co/data-centers/us",
        "notes": "County/city-level public location.",
    },
    {
        "name": "Colossus 1",
        "developer": "xAI",
        "status": "Operating",
        "capacity_mw": 300,
        "location": "Memphis / Shelby County, Tennessee",
        "latitude": 35.1495,
        "longitude": -90.0490,
        "source": "Cleanview public tracker; public reporting",
        "source_url": "https://cleanview.co/data-centers/us",
        "notes": "AI supercomputer/data center; marker is city-level approximate.",
    },
    {
        "name": "Council Bluffs Data Center - Campus 2 West",
        "developer": "Google",
        "status": "Operating",
        "capacity_mw": 300,
        "location": "Council Bluffs / Pottawattamie County, Iowa",
        "latitude": 41.2619,
        "longitude": -95.8608,
        "source": "Cleanview public tracker",
        "source_url": "https://cleanview.co/data-centers/us",
        "notes": "Hyperscale cloud/AI campus; public location is approximate.",
    },
    {
        "name": "Stargate Abilene",
        "developer": "OpenAI / Oracle / Crusoe",
        "status": "Under construction",
        "capacity_mw": 200,
        "location": "Abilene / Taylor County, Texas",
        "latitude": 32.4487,
        "longitude": -99.7331,
        "source": "Epoch AI public analysis; public reporting",
        "source_url": "https://epoch.ai/data-insights/data-center-sizes",
        "notes": "Public reporting describes initial AI capacity expanding through 2026.",
    },
    {
        "name": "Meta Hyperion",
        "developer": "Meta",
        "status": "Planned",
        "capacity_mw": 5238,
        "location": "Richland Parish, Louisiana",
        "latitude": 32.4415,
        "longitude": -91.7540,
        "source": "Meta announcement; Cleanview public tracker",
        "source_url": "https://datacenters.atmeta.com/richland-parish-data-center/",
        "notes": "AI-optimized campus; marker is parish-level approximate.",
    },
    {
        "name": "Oracle / Stargate Michigan Campus",
        "developer": "Oracle / Related Digital",
        "status": "Planned",
        "capacity_mw": 1000,
        "location": "Saline Township, Michigan",
        "latitude": 42.1667,
        "longitude": -83.7816,
        "source": "Business Insider public reporting",
        "source_url": "https://www.businessinsider.com/ai-data-center-saline-michigan-funding-openai-stargate-blackstone-pimco-2026-4",
        "notes": "Reported AI/Stargate-linked campus; marker is township-level approximate.",
    },
    {
        "name": "Google Columbus / New Albany Cluster",
        "developer": "Google",
        "status": "Operating / expanding",
        "capacity_mw": 1000,
        "location": "New Albany / Columbus, Ohio",
        "latitude": 40.0812,
        "longitude": -82.8088,
        "source": "Data Center Richness / SemiAnalysis discussion",
        "source_url": "https://datacenterrichness.substack.com/p/data-centers-2026-the-largest-ai",
        "notes": "Cluster-level marker; public reporting mixes cloud and AI workloads.",
    },
    {
        "name": "PORTS Technology Campus - Phase 2",
        "developer": "SB Energy",
        "status": "Planned",
        "capacity_mw": 9200,
        "location": "Pike County, Ohio",
        "latitude": 39.0681,
        "longitude": -83.0143,
        "source": "Cleanview public tracker",
        "source_url": "https://cleanview.co/data-centers/us",
        "notes": "Large planned data-center campus; county-level public location.",
    },
    {
        "name": "Fermi - Project Matador",
        "developer": "Fermi America",
        "status": "Planned",
        "capacity_mw": 6000,
        "location": "Carson County, Texas",
        "latitude": 35.3456,
        "longitude": -101.3804,
        "source": "Cleanview public tracker",
        "source_url": "https://cleanview.co/data-centers/us",
        "notes": "Large planned data-center campus; county-level public location.",
    },
    {
        "name": "Wonder Valley / Stratos Project - Phase 2",
        "developer": "O'Leary Digital",
        "status": "Planned",
        "capacity_mw": 6000,
        "location": "Box Elder County, Utah",
        "latitude": 41.5102,
        "longitude": -112.0155,
        "source": "Cleanview public tracker",
        "source_url": "https://cleanview.co/data-centers/us",
        "notes": "Large planned data-center campus; county-level public location.",
    },
    {
        "name": "Monarch Compute Campus - Phase 2",
        "developer": "Nscale",
        "status": "Planned",
        "capacity_mw": 6000,
        "location": "Mason County, West Virginia",
        "latitude": 38.8445,
        "longitude": -82.1371,
        "source": "Cleanview public tracker",
        "source_url": "https://cleanview.co/data-centers/us",
        "notes": "Large planned data-center campus; county-level public location.",
    },
]


@dataclass(frozen=True)
class EiaWorkbook:
    label: str
    url: str
    path: Path


def fetch_text(url: str, timeout: int = 30) -> str:
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.exceptions.SSLError:
        warnings.warn(
            "TLS verification failed for EIA. Retrying without certificate "
            "verification because this local Python install may be missing CA roots.",
            RuntimeWarning,
            stacklevel=2,
        )
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(url, timeout=timeout, verify=False)
        response.raise_for_status()
        return response.text


def fetch_binary(url: str, timeout: int = 90) -> bytes:
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.content
    except requests.exceptions.SSLError:
        warnings.warn(
            "TLS verification failed for EIA workbook. Retrying without "
            "certificate verification.",
            RuntimeWarning,
            stacklevel=2,
        )
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(url, timeout=timeout, verify=False)
        response.raise_for_status()
        return response.content


def find_latest_eia860m_workbook() -> tuple[str, str]:
    page = fetch_text(EIA_860M_PAGE)
    page = re.sub(r"<!--.*?-->", "", page, flags=re.DOTALL)
    pattern = re.compile(
        r"<td>\s*([A-Za-z]+\s+\d{4})\s*</td>\s*"
        r"<td>\s*<a\s+href=\"([^\"]+generator\d{4}\.xlsx)\"",
        flags=re.IGNORECASE,
    )
    match = pattern.search(page)
    if not match:
        raise RuntimeError("Could not find an EIA-860M workbook link on the EIA page.")

    label, href = match.groups()
    return label, urljoin(EIA_860M_PAGE, href)


def download_eia_workbook(cache_dir: Path, refresh: bool = False) -> EiaWorkbook:
    label, url = find_latest_eia860m_workbook()
    cache_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(url).name
    workbook_path = cache_dir / filename

    if refresh or not workbook_path.exists():
        workbook_path.write_bytes(fetch_binary(url))

    return EiaWorkbook(label=label, url=url, path=workbook_path)


def load_nuclear_plants(workbook_path: Path) -> gpd.GeoDataFrame:
    generators = pd.read_excel(workbook_path, sheet_name="Operating", header=2)
    nuclear = generators[
        generators["Energy Source Code"].astype(str).str.strip().eq("NUC")
    ].copy()
    if nuclear.empty:
        raise RuntimeError("No nuclear generators were found in the EIA workbook.")

    for column in ["Nameplate Capacity (MW)", "Latitude", "Longitude"]:
        nuclear[column] = pd.to_numeric(nuclear[column], errors="coerce")

    plant_columns = [
        "Plant ID",
        "Plant Name",
        "Plant State",
        "County",
        "Latitude",
        "Longitude",
    ]
    plants = (
        nuclear.dropna(subset=["Latitude", "Longitude"])
        .groupby(plant_columns, dropna=False)
        .agg(
            capacity_mw=("Nameplate Capacity (MW)", "sum"),
            reactor_count=("Generator ID", "count"),
            statuses=("Status", lambda values: "; ".join(sorted(set(map(str, values))))),
        )
        .reset_index()
        .rename(
            columns={
                "Plant ID": "plant_id",
                "Plant Name": "name",
                "Plant State": "state",
                "County": "county",
                "Latitude": "latitude",
                "Longitude": "longitude",
            }
        )
    )
    plants["type"] = "Nuclear power plant"
    plants["source"] = "EIA-860M"
    plants["source_url"] = EIA_860M_PAGE

    return gpd.GeoDataFrame(
        plants,
        geometry=gpd.points_from_xy(plants["longitude"], plants["latitude"]),
        crs="EPSG:4326",
    )


def load_ai_datacenters(csv_path: Path | None = None) -> gpd.GeoDataFrame:
    if csv_path:
        data = pd.read_csv(csv_path)
    else:
        data = pd.DataFrame(DEFAULT_AI_DATACENTERS)

    required = {"name", "latitude", "longitude"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"Datacenter CSV is missing required columns: {sorted(missing)}")

    data["latitude"] = pd.to_numeric(data["latitude"], errors="coerce")
    data["longitude"] = pd.to_numeric(data["longitude"], errors="coerce")
    data = data.dropna(subset=["latitude", "longitude"]).copy()
    data["type"] = "AI/data center"

    for optional in ["developer", "status", "capacity_mw", "location", "source", "source_url", "notes"]:
        if optional not in data:
            data[optional] = ""
    data["capacity_mw"] = pd.to_numeric(data["capacity_mw"], errors="coerce")

    return gpd.GeoDataFrame(
        data,
        geometry=gpd.points_from_xy(data["longitude"], data["latitude"]),
        crs="EPSG:4326",
    )


def radius_from_capacity(capacity_mw: float | int | None, minimum: int = 5, maximum: int = 22) -> int:
    if pd.isna(capacity_mw) or capacity_mw <= 0:
        return minimum
    scaled = minimum + (float(capacity_mw) ** 0.5) / 5
    return int(max(minimum, min(maximum, scaled)))


def popup_table(rows: Iterable[tuple[str, object]]) -> folium.Popup:
    html_rows = []
    for label, value in rows:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        value_text = html.escape(str(value))
        html_rows.append(
            f"<tr><th style='text-align:start;padding-inline-end:10px'>{html.escape(label)}</th>"
            f"<td>{value_text}</td></tr>"
        )
    return folium.Popup(
        "<table style='font-size:13px'>" + "".join(html_rows) + "</table>",
        max_width=420,
    )


def add_title(map_obj: folium.Map, nuclear_count: int, datacenter_count: int, eia_label: str) -> None:
    title = f"""
    <div style="
        position: fixed;
        inset-block-start: 12px;
        inset-inline-start: 50px;
        z-index: 9999;
        background: rgba(255,255,255,0.94);
        border: 1px solid #c9c9c9;
        border-radius: 6px;
        padding: 10px 12px;
        box-shadow: 0 1px 8px rgba(0,0,0,0.18);
        font-family: Arial, sans-serif;
        max-inline-size: 440px;">
      <div style="font-size:16px;font-weight:700;">U.S. AI/Data Centers and Nuclear Power Plants</div>
      <div style="font-size:12px;margin-block-start:4px;">
        {datacenter_count} public AI/data-center markers + {nuclear_count} EIA nuclear plant markers.
        Nuclear source: EIA-860M {html.escape(eia_label)}.
      </div>
      <div style="font-size:12px;margin-block-start:6px;">
        <span style="color:#2563eb;font-weight:700;">●</span> operating/expanding AI-data center
        <span style="color:#f97316;font-weight:700;margin-inline-start:10px;">●</span> planned/under construction
        <span style="color:#15803d;font-weight:700;margin-inline-start:10px;">●</span> nuclear
      </div>
    </div>
    """
    map_obj.get_root().html.add_child(folium.Element(title))


def build_map(
    nuclear_gdf: gpd.GeoDataFrame,
    datacenter_gdf: gpd.GeoDataFrame,
    eia_workbook: EiaWorkbook,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    map_obj = folium.Map(
        location=[39.5, -98.35],
        zoom_start=4,
        tiles="CartoDB positron",
        control_scale=True,
        prefer_canvas=True,
    )

    ai_layer = folium.FeatureGroup(name="AI / data center projects", show=True)
    nuclear_layer = folium.FeatureGroup(name="Nuclear power plants (EIA-860M)", show=True)

    for row in datacenter_gdf.itertuples():
        status = str(getattr(row, "status", ""))
        color = "#2563eb" if "operat" in status.lower() else "#f97316"
        folium.CircleMarker(
            location=[row.latitude, row.longitude],
            radius=radius_from_capacity(getattr(row, "capacity_mw", None)),
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.72,
            weight=2,
            tooltip=f"{row.name} ({getattr(row, 'developer', '')})",
            popup=popup_table(
                [
                    ("Name", row.name),
                    ("Developer", getattr(row, "developer", "")),
                    ("Status", getattr(row, "status", "")),
                    ("Capacity MW", getattr(row, "capacity_mw", "")),
                    ("Location", getattr(row, "location", "")),
                    ("Source", getattr(row, "source", "")),
                    ("Source URL", getattr(row, "source_url", "")),
                    ("Notes", getattr(row, "notes", "")),
                ]
            ),
        ).add_to(ai_layer)

    for row in nuclear_gdf.itertuples():
        folium.CircleMarker(
            location=[row.latitude, row.longitude],
            radius=radius_from_capacity(row.capacity_mw, minimum=5, maximum=18),
            color="#15803d",
            fill=True,
            fill_color="#22c55e",
            fill_opacity=0.65,
            weight=2,
            tooltip=f"{row.name} ({row.state})",
            popup=popup_table(
                [
                    ("Plant", row.name),
                    ("State", row.state),
                    ("County", row.county),
                    ("EIA Plant ID", row.plant_id),
                    ("Capacity MW", round(row.capacity_mw, 1)),
                    ("Generator rows", row.reactor_count),
                    ("Status", row.statuses),
                    ("Source", f"EIA-860M {eia_workbook.label}"),
                    ("Source URL", eia_workbook.url),
                ]
            ),
        ).add_to(nuclear_layer)

    ai_layer.add_to(map_obj)
    nuclear_layer.add_to(map_obj)
    MiniMap(toggle_display=True, position="bottomleft").add_to(map_obj)
    Fullscreen(position="topright").add_to(map_obj)
    folium.LayerControl(collapsed=False).add_to(map_obj)
    add_title(map_obj, len(nuclear_gdf), len(datacenter_gdf), eia_workbook.label)
    map_obj.save(output_path)


def save_supporting_files(
    nuclear_gdf: gpd.GeoDataFrame,
    datacenter_gdf: gpd.GeoDataFrame,
    output_path: Path,
) -> None:
    stem = output_path.with_suffix("")
    nuclear_gdf.drop(columns="geometry").to_csv(f"{stem}_nuclear_plants.csv", index=False)
    datacenter_gdf.drop(columns="geometry").to_csv(f"{stem}_ai_datacenters.csv", index=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an interactive GeoPandas/Folium map of U.S. AI data centers and nuclear plants."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("us_ai_datacenters_nuclear_map.html"),
        help="HTML map output path.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).with_name("cache"),
        help="Directory for cached EIA workbook downloads.",
    )
    parser.add_argument(
        "--datacenters-csv",
        type=Path,
        default=None,
        help=(
            "Optional CSV for the AI/data-center layer. Required columns: "
            "name, latitude, longitude. Useful columns: developer, status, "
            "capacity_mw, location, source, source_url, notes."
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Download the latest EIA workbook even if it is already cached.",
    )
    parser.add_argument(
        "--no-supporting-csv",
        action="store_true",
        help="Only write the HTML map; skip supporting CSV exports.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    eia_workbook = download_eia_workbook(args.cache_dir, refresh=args.refresh)
    nuclear_gdf = load_nuclear_plants(eia_workbook.path)
    datacenter_gdf = load_ai_datacenters(args.datacenters_csv)
    build_map(nuclear_gdf, datacenter_gdf, eia_workbook, args.output)
    if not args.no_supporting_csv:
        save_supporting_files(nuclear_gdf, datacenter_gdf, args.output)

    print(f"Map written to: {args.output.resolve()}")
    print(f"Nuclear plants: {len(nuclear_gdf)} from EIA-860M {eia_workbook.label}")
    print(f"AI/data-center markers: {len(datacenter_gdf)}")
    print("Note: default AI/data-center layer is curated public data, not a complete facility census.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
