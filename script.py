"""
Build an interactive U.S. map of public data-center projects, major data-center
markets, and operating nuclear power plants.

The nuclear layer is read from EIA-860M, the official monthly generator
inventory. The default data-center layer is a curated public landscape layer:
large AI-scale projects, planned megacampuses, and major U.S. markets. Pass
--datacenters-csv to replace it with a project-specific facility list.

This script intentionally uses only the Python standard library. It emits a
standalone Leaflet HTML page that can be embedded on a web page or opened
directly in a browser.

How to use:
1. Run `python script.py` to rebuild the default HTML map and supporting CSVs.
2. Use `python script.py --refresh` to force a fresh EIA workbook download.
3. Use `python script.py --datacenters-csv your_file.csv` to replace the
   default curated data-center layer. That CSV must include `name`,
   `latitude`, and `longitude`; the argument help below lists optional fields.
4. Open the generated `us_ai_datacenters_nuclear_map.html` in a browser.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html as html_lib
import json
import math
import posixpath
import re
import ssl
import sys
import textwrap
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from zipfile import ZipFile


# Source pages used by the downloader and by the source links in the map.
EIA_860M_PAGE = "https://www.eia.gov/electricity/data/eia860m/"
CLEANVIEW_US_DATACENTERS_URL = "https://cleanview.co/data-centers/us"

# High-level tracker numbers shown in the intro and detail panels.
LANDSCAPE_SUMMARY = {
    "source": "Cleanview public U.S. data-center tracker",
    "source_url": CLEANVIEW_US_DATACENTERS_URL,
    "as_of": "June 2026",
    "tracked_data_centers": 2666,
    "operating_data_centers": 1100,
    "planned_data_centers": 1526,
    "operating_power_mw": 51870,
    "planned_power_mw": 357035,
}


# Fallback data-center and market markers used when no CSV is supplied.
# Each dictionary becomes one marker on the map. Latitude/longitude should be
# the best public location available, and `landscape_weight` controls marker
# size when `capacity_mw` is blank for market-level markers.
DEFAULT_DATA_CENTERS = [
    {
        "name": "Fairwater 1 - Mount Pleasant",
        "developer": "Microsoft",
        "status": "Operating",
        "category": "AI megaproject",
        "capacity_mw": 400,
        "capacity_label": "400 MW public tracker estimate",
        "landscape_weight": 18,
        "location": "Mount Pleasant / Racine County, Wisconsin",
        "latitude": 42.7261,
        "longitude": -87.7829,
        "precision": "city/project-level approximate",
        "source": "Cleanview public tracker; Microsoft public reporting",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "AI-scale data-center campus. Public marker is approximate.",
    },
    {
        "name": "Project Zodiac - Fort Wayne / Allen County",
        "developer": "Google",
        "status": "Operating / expanding",
        "category": "Hyperscale AI/cloud project",
        "capacity_mw": 400,
        "capacity_label": "400 MW public tracker estimate",
        "landscape_weight": 18,
        "location": "Fort Wayne / Allen County, Indiana",
        "latitude": 41.0793,
        "longitude": -85.1394,
        "precision": "city/county-level approximate",
        "source": "Cleanview public tracker",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Public reporting and trackers place this as a major Google campus marker.",
    },
    {
        "name": "Fairwater 2 - Atlanta",
        "developer": "Microsoft",
        "status": "Operating / expanding",
        "category": "AI megaproject",
        "capacity_mw": 350,
        "capacity_label": "350 MW public tracker estimate",
        "landscape_weight": 17,
        "location": "Atlanta / Fulton County, Georgia",
        "latitude": 33.7490,
        "longitude": -84.3880,
        "precision": "city-level approximate",
        "source": "Cleanview public tracker",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Atlanta-area AI/data-center marker. Public location is approximate.",
    },
    {
        "name": "Colossus 1",
        "developer": "xAI",
        "status": "Operating",
        "category": "AI supercomputer",
        "capacity_mw": 300,
        "capacity_label": "300 MW public tracker estimate",
        "landscape_weight": 17,
        "location": "Memphis / Shelby County, Tennessee",
        "latitude": 35.1495,
        "longitude": -90.0490,
        "precision": "city-level approximate",
        "source": "Cleanview public tracker; public reporting",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "AI supercomputer/data-center marker. Public marker is city-level approximate.",
    },
    {
        "name": "Council Bluffs Data Center - Campus 2 West",
        "developer": "Google",
        "status": "Operating / expanding",
        "category": "Hyperscale cloud/AI campus",
        "capacity_mw": 300,
        "capacity_label": "300 MW public tracker estimate",
        "landscape_weight": 17,
        "location": "Council Bluffs / Pottawattamie County, Iowa",
        "latitude": 41.2619,
        "longitude": -95.8608,
        "precision": "city/project-level approximate",
        "source": "Cleanview public tracker; Google data-center locations",
        "source_url": "https://www.google.com/about/datacenters/locations/",
        "notes": "Long-running hyperscale market with continued cloud and AI relevance.",
    },
    {
        "name": "Stargate Abilene",
        "developer": "OpenAI / Oracle / Crusoe",
        "status": "Under construction",
        "category": "AI megaproject",
        "capacity_mw": 200,
        "capacity_label": "200 MW initial public estimate",
        "landscape_weight": 16,
        "location": "Abilene / Taylor County, Texas",
        "latitude": 32.4487,
        "longitude": -99.7331,
        "precision": "city/project-level approximate",
        "source": "Epoch AI public analysis; public reporting",
        "source_url": "https://epoch.ai/data-insights/data-center-sizes",
        "notes": "Public reporting describes initial AI capacity with staged expansion.",
    },
    {
        "name": "Meta Hyperion - Richland Parish",
        "developer": "Meta",
        "status": "Planned",
        "category": "AI-optimized data-center campus",
        "capacity_mw": 2000,
        "capacity_label": "2,000+ MW design target",
        "landscape_weight": 25,
        "location": "Richland Parish, Louisiana",
        "latitude": 32.4415,
        "longitude": -91.7540,
        "precision": "parish-level approximate",
        "source": "Meta Richland Parish data-center announcement",
        "source_url": "https://datacenters.atmeta.com/richland-parish-data-center/",
        "notes": "Meta says the campus is designed for more than 2 GW of compute load.",
    },
    {
        "name": "PORTS Technology Campus - Phase 2",
        "developer": "SB Energy",
        "status": "Planned",
        "category": "Large planned data-center campus",
        "capacity_mw": 9200,
        "capacity_label": "9,200 MW planned public tracker estimate",
        "landscape_weight": 31,
        "location": "Pike County, Ohio",
        "latitude": 39.0681,
        "longitude": -83.0143,
        "precision": "county-level approximate",
        "source": "Cleanview public tracker",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Very large planned campus marker. Capacity is a public tracker estimate, not an EIA operating load.",
    },
    {
        "name": "Fermi - Project Matador",
        "developer": "Fermi America",
        "status": "Planned",
        "category": "Large planned data-center campus",
        "capacity_mw": 6000,
        "capacity_label": "6,000 MW planned public tracker estimate",
        "landscape_weight": 29,
        "location": "Carson County, Texas",
        "latitude": 35.3456,
        "longitude": -101.3804,
        "precision": "county-level approximate",
        "source": "Cleanview public tracker",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Large planned compute campus marker. Public project details remain early-stage.",
    },
    {
        "name": "Wonder Valley / Stratos Project - Phase 2",
        "developer": "O'Leary Digital",
        "status": "Planned",
        "category": "Large planned data-center campus",
        "capacity_mw": 6000,
        "capacity_label": "6,000 MW planned public tracker estimate",
        "landscape_weight": 29,
        "location": "Box Elder County, Utah",
        "latitude": 41.5102,
        "longitude": -112.0155,
        "precision": "county-level approximate",
        "source": "Cleanview public tracker",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Large planned campus marker. Public project details remain early-stage.",
    },
    {
        "name": "Monarch Compute Campus - Phase 2",
        "developer": "Nscale",
        "status": "Planned",
        "category": "Large planned data-center campus",
        "capacity_mw": 6000,
        "capacity_label": "6,000 MW planned public tracker estimate",
        "landscape_weight": 29,
        "location": "Mason County, West Virginia",
        "latitude": 38.8445,
        "longitude": -82.1371,
        "precision": "county-level approximate",
        "source": "Cleanview public tracker",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Large planned campus marker. Public project details remain early-stage.",
    },
    {
        "name": "Northern Virginia / Data Center Alley",
        "developer": "Multiple operators",
        "status": "Operating market",
        "category": "Major market hub",
        "capacity_mw": "",
        "capacity_label": "Market marker",
        "landscape_weight": 28,
        "location": "Ashburn / Loudoun County, Virginia",
        "latitude": 39.0438,
        "longitude": -77.4874,
        "precision": "market-center approximate",
        "source": "Cleanview public tracker; public market summaries",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Represents the dominant Northern Virginia data-center market, not a single facility.",
    },
    {
        "name": "Phoenix Metro Data-Center Market",
        "developer": "Multiple operators",
        "status": "Operating market",
        "category": "Major market hub",
        "capacity_mw": "",
        "capacity_label": "Market marker",
        "landscape_weight": 22,
        "location": "Phoenix / Maricopa County, Arizona",
        "latitude": 33.4484,
        "longitude": -112.0740,
        "precision": "market-center approximate",
        "source": "Cleanview public tracker; public market summaries",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Represents a fast-growing Southwest data-center market, not a single facility.",
    },
    {
        "name": "Dallas-Fort Worth Data-Center Market",
        "developer": "Multiple operators",
        "status": "Operating market",
        "category": "Major market hub",
        "capacity_mw": "",
        "capacity_label": "Market marker",
        "landscape_weight": 21,
        "location": "Dallas-Fort Worth, Texas",
        "latitude": 32.7767,
        "longitude": -96.7970,
        "precision": "market-center approximate",
        "source": "Cleanview public tracker; public market summaries",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Represents the North Texas data-center market, not a single facility.",
    },
    {
        "name": "Columbus / New Albany Data-Center Market",
        "developer": "Multiple operators",
        "status": "Operating / expanding market",
        "category": "Major market hub",
        "capacity_mw": "",
        "capacity_label": "Market marker",
        "landscape_weight": 20,
        "location": "New Albany / Columbus, Ohio",
        "latitude": 40.0812,
        "longitude": -82.8088,
        "precision": "market-center approximate",
        "source": "Cleanview public tracker; Google and hyperscale public locations",
        "source_url": "https://www.google.com/about/datacenters/locations/",
        "notes": "Represents the central Ohio hyperscale cluster, not a single facility.",
    },
    {
        "name": "Hillsboro Data-Center Market",
        "developer": "Multiple operators",
        "status": "Operating market",
        "category": "Major market hub",
        "capacity_mw": "",
        "capacity_label": "Market marker",
        "landscape_weight": 18,
        "location": "Hillsboro / Portland Metro, Oregon",
        "latitude": 45.5229,
        "longitude": -122.9898,
        "precision": "market-center approximate",
        "source": "Cleanview public tracker; public market summaries",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Represents the Hillsboro/Portland market, not a single facility.",
    },
    {
        "name": "Silicon Valley / Santa Clara Market",
        "developer": "Multiple operators",
        "status": "Operating market",
        "category": "Major market hub",
        "capacity_mw": "",
        "capacity_label": "Market marker",
        "landscape_weight": 18,
        "location": "Santa Clara / San Jose, California",
        "latitude": 37.3541,
        "longitude": -121.9552,
        "precision": "market-center approximate",
        "source": "Cleanview public tracker; public market summaries",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Represents the Silicon Valley data-center market, not a single facility.",
    },
    {
        "name": "Chicago / Elk Grove Data-Center Market",
        "developer": "Multiple operators",
        "status": "Operating market",
        "category": "Major market hub",
        "capacity_mw": "",
        "capacity_label": "Market marker",
        "landscape_weight": 17,
        "location": "Chicago / Elk Grove Village, Illinois",
        "latitude": 41.8781,
        "longitude": -87.6298,
        "precision": "market-center approximate",
        "source": "Cleanview public tracker; public market summaries",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Represents the Chicago-area data-center market, not a single facility.",
    },
    {
        "name": "Central Washington Data-Center Market",
        "developer": "Multiple operators",
        "status": "Operating market",
        "category": "Major market hub",
        "capacity_mw": "",
        "capacity_label": "Market marker",
        "landscape_weight": 16,
        "location": "Quincy / Moses Lake, Washington",
        "latitude": 47.2343,
        "longitude": -119.8526,
        "precision": "market-center approximate",
        "source": "Cleanview public tracker; public market summaries",
        "source_url": CLEANVIEW_US_DATACENTERS_URL,
        "notes": "Represents the central Washington hydro-linked data-center market, not a single facility.",
    },
]


@dataclass(frozen=True)
class EiaWorkbook:
    """Bookkeeping for the EIA workbook currently feeding the nuclear layer."""

    label: str
    url: str
    path: Path
    from_cache: bool = False


def fetch_url(url: str, timeout: int = 45, binary: bool = False) -> str | bytes:
    """Download text or binary content with a map-specific user agent."""

    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; datacenter-nuclear-map/2.0; "
                "+https://www.incompas.org/)"
            )
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except ssl.SSLError:
        context = ssl._create_unverified_context()
        with urlopen(request, timeout=timeout, context=context) as response:
            payload = response.read()
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if not isinstance(reason, ssl.SSLError):
            raise
        context = ssl._create_unverified_context()
        with urlopen(request, timeout=timeout, context=context) as response:
            payload = response.read()

    if binary:
        return payload
    return payload.decode("utf-8", errors="replace")


def find_latest_eia860m_workbook() -> tuple[str, str]:
    """Find the newest monthly generator workbook linked from the EIA page."""

    page = fetch_url(EIA_860M_PAGE, timeout=30, binary=False)
    assert isinstance(page, str)
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


def label_from_workbook_filename(path: Path) -> str:
    """Turn a cached workbook filename like `may_generator2026.xlsx` into a label."""

    match = re.search(r"([a-z]+)_generator(\d{4})", path.name, flags=re.IGNORECASE)
    if not match:
        return path.stem
    month, year = match.groups()
    return f"{month.title()} {year}"


def find_newest_cached_workbook(cache_dir: Path) -> Path | None:
    """Return the most recently modified cached workbook, if one exists."""

    workbooks = sorted(
        cache_dir.glob("*_generator*.xlsx"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    return workbooks[0] if workbooks else None


def download_eia_workbook(cache_dir: Path, refresh: bool = False) -> EiaWorkbook:
    """Download or reuse the EIA workbook that powers the nuclear-plant layer."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        label, url = find_latest_eia860m_workbook()
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
        cached = find_newest_cached_workbook(cache_dir)
        if cached:
            warnings.warn(
                f"Could not reach EIA-860M ({exc}). Using cached workbook {cached.name}.",
                RuntimeWarning,
                stacklevel=2,
            )
            return EiaWorkbook(
                label=label_from_workbook_filename(cached),
                url=EIA_860M_PAGE,
                path=cached,
                from_cache=True,
            )
        raise

    filename = Path(urlparse(url).path).name
    workbook_path = cache_dir / filename
    if refresh or not workbook_path.exists():
        payload = fetch_url(url, timeout=120, binary=True)
        assert isinstance(payload, bytes)
        workbook_path.write_bytes(payload)

    return EiaWorkbook(label=label, url=url, path=workbook_path)


# Namespaces used while reading `.xlsx` files directly as zipped XML. This
# keeps the script dependency-free instead of requiring a spreadsheet library.
XLSX_MAIN_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
XLSX_REL_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
XLSX_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def read_shared_strings(workbook: ZipFile) -> list[str]:
    """Read Excel's shared string table so worksheet cells can resolve text."""

    try:
        root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    strings: list[str] = []
    for item in root.findall("main:si", XLSX_MAIN_NS):
        strings.append("".join(item.itertext()))
    return strings


def workbook_sheet_paths(workbook: ZipFile) -> dict[str, str]:
    """Map each visible Excel sheet name to its internal XML path."""

    workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
    rels_root = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    rels = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels_root.findall("rel:Relationship", XLSX_REL_NS)
    }

    paths: dict[str, str] = {}
    for sheet in workbook_root.findall("main:sheets/main:sheet", XLSX_MAIN_NS):
        name = sheet.attrib["name"]
        rel_id = sheet.attrib[f"{{{XLSX_OFFICE_REL}}}id"]
        target = rels[rel_id]
        if target.startswith("/"):
            sheet_path = target.lstrip("/")
        else:
            sheet_path = posixpath.normpath(posixpath.join("xl", target))
        paths[name] = sheet_path
    return paths


def excel_column_index(cell_ref: str) -> int:
    """Convert an Excel cell reference such as `C12` into a zero-based column."""

    match = re.match(r"([A-Z]+)", cell_ref)
    if not match:
        raise ValueError(f"Invalid Excel cell reference: {cell_ref}")
    index = 0
    for letter in match.group(1):
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index - 1


def xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    """Return a worksheet cell's text, resolving shared-string references."""

    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(cell.itertext())

    value = cell.find("main:v", XLSX_MAIN_NS)
    if value is None or value.text is None:
        return ""

    text = value.text
    if cell_type == "s":
        return shared_strings[int(text)]
    return text


def read_xlsx_sheet(path: Path, sheet_name: str, header_row: int) -> list[dict[str, str]]:
    """Read one worksheet into dictionaries keyed by the requested header row."""

    with ZipFile(path) as workbook:
        shared_strings = read_shared_strings(workbook)
        sheet_paths = workbook_sheet_paths(workbook)
        if sheet_name not in sheet_paths:
            raise RuntimeError(f"Workbook does not contain a {sheet_name!r} sheet.")

        sheet_root = ET.fromstring(workbook.read(sheet_paths[sheet_name]))
        headers: list[str] | None = None
        rows: list[dict[str, str]] = []

        for row in sheet_root.findall(".//main:sheetData/main:row", XLSX_MAIN_NS):
            row_number = int(row.attrib.get("r", "0"))
            values: dict[int, str] = {}
            for cell in row.findall("main:c", XLSX_MAIN_NS):
                values[excel_column_index(cell.attrib["r"])] = xlsx_cell_value(
                    cell, shared_strings
                )

            if row_number == header_row:
                max_index = max(values.keys()) if values else -1
                headers = [str(values.get(i, "")).strip() for i in range(max_index + 1)]
                continue

            if row_number <= header_row or headers is None:
                continue

            if not values:
                continue

            max_index = min(len(headers), max(values.keys()) + 1)
            record = {
                headers[i]: values.get(i, "")
                for i in range(max_index)
                if i < len(headers) and headers[i]
            }
            if any(str(value).strip() for value in record.values()):
                rows.append(record)

    return rows


def safe_float(value: Any) -> float | None:
    """Parse a number-like value and return None for blanks or invalid values."""

    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"na", "n/a", "none", "null", "-"}:
        return None
    text = text.rstrip("+")
    try:
        return float(text)
    except ValueError:
        return None


def normalize_status_group(status: Any, category: Any = "") -> str:
    """Collapse many public status/category phrases into the map filter groups."""

    combined = f"{status} {category}".lower()
    if "market" in combined or "hub" in combined:
        return "Market hub"
    if "under construction" in combined or "construction" in combined:
        return "Under construction"
    if "planned" in combined or "proposed" in combined:
        return "Planned"
    if "operat" in combined or "expanding" in combined:
        return "Operating"
    return "Other"


def format_mw(value: Any) -> str:
    """Format megawatts for labels shown in the map UI."""

    number = safe_float(value)
    if number is None:
        return ""
    return f"{number:,.0f} MW"


def load_nuclear_plants(workbook_path: Path) -> list[dict[str, Any]]:
    """Load and group nuclear generator rows into one marker per plant."""

    rows = read_xlsx_sheet(workbook_path, sheet_name="Operating", header_row=3)
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}

    for row in rows:
        if str(row.get("Energy Source Code", "")).strip() != "NUC":
            continue

        latitude = safe_float(row.get("Latitude"))
        longitude = safe_float(row.get("Longitude"))
        if latitude is None or longitude is None:
            continue

        key = (
            str(row.get("Plant ID", "")).strip(),
            str(row.get("Plant Name", "")).strip(),
            str(row.get("Plant State", "")).strip(),
            str(row.get("County", "")).strip(),
            latitude,
            longitude,
        )
        plant = grouped.setdefault(
            key,
            {
                "plant_id": key[0],
                "name": key[1],
                "state": key[2],
                "county": key[3],
                "latitude": latitude,
                "longitude": longitude,
                "capacity_mw": 0.0,
                "unit_ids": set(),
                "statuses": set(),
                "type": "Nuclear power plant",
                "source": "EIA-860M",
                "source_url": EIA_860M_PAGE,
            },
        )

        plant["capacity_mw"] += safe_float(row.get("Nameplate Capacity (MW)")) or 0
        generator_id = str(row.get("Generator ID", "")).strip()
        if generator_id:
            plant["unit_ids"].add(generator_id)
        status = str(row.get("Status", "")).strip()
        if status:
            plant["statuses"].add(status)

    plants: list[dict[str, Any]] = []
    for plant in grouped.values():
        plant["capacity_mw"] = round(float(plant["capacity_mw"]), 1)
        plant["unit_count"] = len(plant.pop("unit_ids"))
        plant["statuses"] = "; ".join(sorted(plant["statuses"]))
        plants.append(plant)

    if not plants:
        raise RuntimeError("No operating nuclear generator rows were found in EIA-860M.")

    return sorted(plants, key=lambda item: (item["state"], item["name"]))


def normalize_datacenter_record(record: dict[str, Any]) -> dict[str, Any]:
    """Fill optional fields and convert numeric strings for one data-center row."""

    normalized = dict(record)
    for field in [
        "developer",
        "status",
        "category",
        "capacity_mw",
        "capacity_label",
        "landscape_weight",
        "location",
        "source",
        "source_url",
        "notes",
        "precision",
    ]:
        normalized.setdefault(field, "")

    normalized["latitude"] = safe_float(normalized.get("latitude"))
    normalized["longitude"] = safe_float(normalized.get("longitude"))
    normalized["capacity_mw"] = safe_float(normalized.get("capacity_mw"))
    normalized["landscape_weight"] = safe_float(normalized.get("landscape_weight"))
    normalized["status_group"] = normalize_status_group(
        normalized.get("status"), normalized.get("category")
    )
    if not normalized.get("capacity_label"):
        normalized["capacity_label"] = format_mw(normalized.get("capacity_mw"))
    normalized["type"] = "Data center / compute marker"
    return normalized


def load_data_centers(csv_path: Path | None = None) -> list[dict[str, Any]]:
    """Load either the default data-center layer or a user-supplied CSV layer."""

    if csv_path:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            records = list(csv.DictReader(handle))
    else:
        records = [dict(item) for item in DEFAULT_DATA_CENTERS]

    required = {"name", "latitude", "longitude"}
    if records:
        missing = required.difference(records[0].keys())
        if missing:
            raise ValueError(f"Datacenter CSV is missing required columns: {sorted(missing)}")

    normalized = [normalize_datacenter_record(record) for record in records]
    return [
        record
        for record in normalized
        if record["latitude"] is not None and record["longitude"] is not None
    ]


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Measure great-circle distance between two latitude/longitude points."""

    radius_miles = 3958.7613
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return radius_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def enrich_data_center_proximity(
    data_centers: list[dict[str, Any]], nuclear_plants: list[dict[str, Any]]
) -> None:
    """Attach nearest-nuclear-plant fields to each data-center marker in place."""

    for site in data_centers:
        nearest: dict[str, Any] | None = None
        nearest_distance = float("inf")
        for plant in nuclear_plants:
            distance = haversine_miles(
                float(site["latitude"]),
                float(site["longitude"]),
                float(plant["latitude"]),
                float(plant["longitude"]),
            )
            if distance < nearest_distance:
                nearest_distance = distance
                nearest = plant

        if nearest:
            site["nearest_nuclear_name"] = nearest["name"]
            site["nearest_nuclear_state"] = nearest["state"]
            site["nearest_nuclear_capacity_mw"] = nearest["capacity_mw"]
            site["nearest_nuclear_distance_mi"] = round(nearest_distance, 1)
            site["nearest_nuclear_latitude"] = nearest["latitude"]
            site["nearest_nuclear_longitude"] = nearest["longitude"]


def total_capacity(records: Iterable[dict[str, Any]]) -> float:
    """Sum `capacity_mw` across records, treating blanks as zero."""

    return sum(safe_float(record.get("capacity_mw")) or 0 for record in records)


def fmt_number(value: float | int) -> str:
    """Format whole numbers for generated HTML text."""

    return f"{value:,.0f}"


def fmt_gw(mw_value: float | int) -> str:
    """Convert megawatts to a one-decimal gigawatt label."""

    return f"{float(mw_value) / 1000:,.1f} GW"


def json_for_html(data: Any) -> str:
    """Serialize JSON safely for inline use inside the generated script tag."""

    return json.dumps(data, ensure_ascii=True, allow_nan=False).replace("</", "<\\/")


def html_escape(value: Any) -> str:
    """Escape generated values before they are inserted into HTML text."""

    return html_lib.escape(str(value), quote=True)


def render_map_html(
    nuclear_plants: list[dict[str, Any]],
    data_centers: list[dict[str, Any]],
    eia_workbook: EiaWorkbook,
) -> str:
    """Render the complete standalone HTML file from data and template text."""

    generated_at = dt.datetime.now().strftime("%B %d, %Y %I:%M %p")
    nuclear_capacity_mw = total_capacity(nuclear_plants)
    dc_capacity_mw = total_capacity(data_centers)
    cache_note = "cached " if eia_workbook.from_cache else ""

    template = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>U.S. Data Centers and Nuclear Power Landscape</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css">
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css">
  <!-- Page-specific styling lives here so the generated HTML is portable. -->
  <style>
    /* Theme variables used by panels, controls, markers, and map overlays. */
    :root {
      --ink: #0c1222;
      --muted: #5e6a80;
      --panel: rgba(255, 255, 255, 0.94);
      --panel-strong: rgba(255, 255, 255, 0.98);
      --line: rgba(15, 23, 42, 0.14);
      --blue: #00a6ff;
      --amber: #ffb000;
      --magenta: #ff3d7f;
      --violet: #8b5cf6;
      --green: #21c55d;
      --teal: #00d4b8;
      --shadow: 0 18px 44px rgba(8, 18, 38, 0.22);
    }

    /* Make the browser viewport and the Leaflet map fill the page. */
    html,
    body {
      block-size: 100%;
      margin: 0;
      background: #08111f;
      color: var(--ink);
      font-family: Inter, "Segoe UI", Arial, sans-serif;
      letter-spacing: 0;
    }

    /* Leaflet attaches its interactive map to this element. */
    #map {
      block-size: 100%;
      min-block-size: 720px;
      inline-size: 100%;
      background: #101827;
    }

    .leaflet-container {
      font-family: Inter, "Segoe UI", Arial, sans-serif;
    }

    /* Floating left-side UI that stays clickable above the map. */
    .ui-shell {
      position: fixed;
      inset-block-start: 18px;
      inset-inline-start: 18px;
      z-index: 900;
      inline-size: min(390px, calc(100vw - 36px));
      display: grid;
      gap: 10px;
      pointer-events: none;
    }

    /* Shared panel treatment for the intro, controls, and detail card. */
    .panel {
      pointer-events: auto;
      background: var(--panel);
      border: 1px solid rgba(255, 255, 255, 0.78);
      border-radius: 8px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
    }

    /* Top summary panel with title text and live/generated metrics. */
    .intro-panel {
      overflow: hidden;
    }

    .intro-band {
      padding: 14px 16px 12px;
      color: #ffffff;
      background:
        linear-gradient(135deg, rgba(0, 166, 255, 0.96), rgba(255, 61, 127, 0.92) 52%, rgba(33, 197, 93, 0.9)),
        #14315a;
    }

    .eyebrow {
      margin-block-end: 5px;
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      opacity: 0.9;
    }

    h1 {
      margin: 0;
      font-size: 24px;
      line-height: 1.06;
      letter-spacing: 0;
    }

    .summary {
      margin: 8px 0 0;
      max-inline-size: 35rem;
      color: rgba(255, 255, 255, 0.91);
      font-size: 13px;
      line-height: 1.35;
    }

    .metric-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 1px;
      background: var(--line);
      border-block-start: 1px solid var(--line);
    }

    .metric {
      min-inline-size: 0;
      padding: 10px 12px;
      background: var(--panel-strong);
    }

    .metric strong {
      display: block;
      color: #0f172a;
      font-size: 18px;
      line-height: 1.05;
      white-space: nowrap;
    }

    .metric span {
      display: block;
      margin-block-start: 3px;
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
      line-height: 1.15;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    /* Search box, status filters, layer toggles, and source note. */
    .control-panel {
      padding: 12px;
      display: grid;
      gap: 10px;
    }

    .search-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
    }

    .search-row input {
      min-inline-size: 0;
      border: 1px solid rgba(15, 23, 42, 0.16);
      border-radius: 8px;
      padding: 10px 11px;
      color: var(--ink);
      background: #ffffff;
      font: inherit;
      font-size: 13px;
      outline: none;
    }

    .search-row input:focus {
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(0, 166, 255, 0.16);
    }

    .mini-count {
      min-inline-size: 68px;
      border-radius: 8px;
      padding: 9px 10px;
      color: #ffffff;
      text-align: center;
      font-size: 12px;
      font-weight: 800;
      background: #111827;
    }

    .chip-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 7px;
    }

    .chip,
    .layer-toggle {
      display: flex;
      min-inline-size: 0;
      align-items: center;
      gap: 7px;
      border: 1px solid rgba(15, 23, 42, 0.13);
      border-radius: 8px;
      padding: 8px 9px;
      background: rgba(255, 255, 255, 0.82);
      color: #172033;
      font-size: 12px;
      font-weight: 750;
      line-height: 1.15;
      cursor: pointer;
      user-select: none;
    }

    .chip input,
    .layer-toggle input {
      accent-color: #0ea5e9;
      inline-size: 14px;
      block-size: 14px;
      flex: 0 0 auto;
    }

    .swatch {
      inline-size: 11px;
      block-size: 11px;
      flex: 0 0 auto;
      border-radius: 999px;
      background: var(--chip-color);
      box-shadow: 0 0 0 3px var(--chip-glow);
    }

    .layer-row {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 7px;
    }

    .layer-toggle {
      justify-content: center;
      padding: 8px;
    }

    .layer-toggle.active,
    .chip:has(input:checked) {
      border-color: rgba(14, 165, 233, 0.42);
      background: linear-gradient(180deg, #ffffff, #eef9ff);
    }

    .source-line {
      color: #5e6a80;
      font-size: 11px;
      line-height: 1.35;
    }

    .source-line a {
      color: #0369a1;
      font-weight: 750;
      text-decoration: none;
    }

    /* Detail panel that updates when a marker is clicked. */
    .detail-panel {
      display: grid;
      gap: 8px;
      padding: 12px;
    }

    .detail-title {
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 8px;
    }

    .detail-title h2 {
      margin: 0;
      font-size: 15px;
      line-height: 1.18;
    }

    .pill {
      flex: 0 0 auto;
      border-radius: 999px;
      padding: 4px 8px;
      color: #ffffff;
      background: #0f172a;
      font-size: 10px;
      font-weight: 850;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    .detail-body {
      color: #344054;
      font-size: 12px;
      line-height: 1.45;
    }

    .detail-list {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 5px 9px;
      margin: 0;
      font-size: 12px;
    }

    .detail-list dt {
      color: #64748b;
      font-weight: 800;
    }

    .detail-list dd {
      min-inline-size: 0;
      margin: 0;
      color: #111827;
    }

    /* Custom Leaflet marker and cluster visuals. */
    .map-marker {
      position: relative;
      display: grid;
      place-items: center;
      inline-size: var(--size);
      block-size: var(--size);
      border: 2px solid rgba(255, 255, 255, 0.92);
      border-radius: 999px;
      color: #ffffff;
      background: radial-gradient(circle at 35% 28%, #ffffff 0 7%, var(--marker) 35%, #172033 100%);
      box-shadow: 0 0 0 4px var(--glow), 0 9px 24px rgba(4, 12, 26, 0.28), 0 0 26px var(--glow);
      transform: translate3d(0, 0, 0);
    }

    .map-marker::after {
      position: absolute;
      inset: -7px;
      content: "";
      border: 1px solid var(--glow);
      border-radius: inherit;
      animation: pulse 2.8s ease-out infinite;
    }

    .marker-label {
      position: relative;
      z-index: 1;
      font-size: 9px;
      font-weight: 900;
      line-height: 1;
      text-shadow: 0 1px 2px rgba(0, 0, 0, 0.35);
    }

    .nuclear-marker {
      background: radial-gradient(circle at 35% 28%, #eafff1 0 8%, var(--green) 38%, #0e3d27 100%);
    }

    @keyframes pulse {
      0% {
        opacity: 0.8;
        transform: scale(0.88);
      }
      100% {
        opacity: 0;
        transform: scale(1.55);
      }
    }

    .cluster-wrap {
      background: transparent;
      border: 0;
    }

    .cluster-icon {
      display: grid;
      place-items: center;
      inline-size: var(--size);
      block-size: var(--size);
      border: 2px solid rgba(255, 255, 255, 0.9);
      border-radius: 999px;
      color: #ffffff;
      background: radial-gradient(circle at 35% 28%, #ffffff 0 7%, #2dd4bf 28%, #2563eb 62%, #0f172a 100%);
      box-shadow: 0 0 0 5px rgba(45, 212, 191, 0.2), 0 12px 30px rgba(15, 23, 42, 0.28);
      text-align: center;
    }

    .cluster-icon b {
      display: block;
      font-size: 15px;
      line-height: 1;
    }

    .cluster-icon span {
      display: block;
      margin-block-start: 1px;
      font-size: 8px;
      font-weight: 800;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    /* Popup, tooltip, and built-in Leaflet control styling. */
    .leaflet-popup-content-wrapper {
      border-radius: 8px;
      box-shadow: 0 18px 44px rgba(15, 23, 42, 0.26);
    }

    .leaflet-popup-content {
      inline-size: 310px !important;
      margin: 14px;
      color: #172033;
    }

    .popup-title {
      margin: 0 0 8px;
      font-size: 16px;
      line-height: 1.16;
    }

    .popup-kicker {
      margin-block-end: 5px;
      color: #64748b;
      font-size: 10px;
      font-weight: 850;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    .popup-table {
      inline-size: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }

    .popup-table th,
    .popup-table td {
      padding: 5px 0;
      border-block-start: 1px solid rgba(15, 23, 42, 0.08);
      vertical-align: start;
    }

    .popup-table th {
      inline-size: 34%;
      padding-inline-end: 10px;
      color: #64748b;
      font-weight: 850;
      text-align: start;
    }

    .popup-table a {
      color: #0369a1;
      font-weight: 800;
      text-decoration: none;
    }

    .leaflet-tooltip {
      border: 0;
      border-radius: 7px;
      color: #ffffff;
      background: rgba(15, 23, 42, 0.9);
      box-shadow: 0 8px 22px rgba(15, 23, 42, 0.2);
      font-weight: 750;
    }

    .leaflet-control-zoom,
    .leaflet-control-layers {
      border: 0 !important;
      box-shadow: var(--shadow) !important;
    }

    .leaflet-control-attribution {
      color: #334155;
      background: rgba(255, 255, 255, 0.84) !important;
      font-size: 10px;
    }

    /* Compact layout adjustments for narrow screens. */
    @media (max-width: 780px) {
      #map {
        min-block-size: 780px;
      }

      .ui-shell {
        inset-block-start: 10px;
        inset-inline-start: 10px;
        inline-size: calc(100vw - 20px);
      }

      h1 {
        font-size: 21px;
      }

      .metric-grid {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }

      .detail-panel {
        display: none;
      }
    }
  </style>
</head>
<body>
  <!-- Leaflet mounts the map here; the controls below float on top of it. -->
  <div id="map" role="application" aria-label="U.S. data-center and nuclear power landscape map"></div>

  <!-- User controls: overview metrics, filters, layer switches, and selected-marker details. -->
  <aside class="ui-shell" aria-label="Map controls">
    <!-- Generated summary counts and capacity totals. -->
    <section class="panel intro-panel">
      <div class="intro-band">
        <div class="eyebrow">INCOMPAS infrastructure landscape</div>
        <h1>Data Centers and Nuclear Power</h1>
        <p class="summary">
          __DC_MARKER_COUNT__ public project and market markers, __NUCLEAR_MARKER_COUNT__ operating nuclear plant markers,
          and nearest-plant distance lines for power-landscape context.
        </p>
      </div>
      <div class="metric-grid">
        <div class="metric">
          <strong>__DC_CAPACITY_GW__</strong>
          <span>shown public data-center power</span>
        </div>
        <div class="metric">
          <strong>__NUCLEAR_CAPACITY_GW__</strong>
          <span>shown nuclear nameplate</span>
        </div>
        <div class="metric">
          <strong>__TRACKED_DC_TOTAL__</strong>
          <span>U.S. sites tracked</span>
        </div>
      </div>
    </section>

    <!-- Search, status filters, optional overlay layers, and data sources. -->
    <section class="panel control-panel">
      <div class="search-row">
        <input id="siteSearch" type="search" placeholder="Search name, company, state, market" autocomplete="off">
        <div class="mini-count"><span id="shownCount">0</span> shown</div>
      </div>

      <div class="chip-grid" id="statusFilters">
        <label class="chip" style="--chip-color:#00a6ff;--chip-glow:rgba(0,166,255,0.18)">
          <input type="checkbox" value="Operating" checked>
          <span class="swatch"></span>
          <span>Operating</span>
        </label>
        <label class="chip" style="--chip-color:#ffb000;--chip-glow:rgba(255,176,0,0.2)">
          <input type="checkbox" value="Under construction" checked>
          <span class="swatch"></span>
          <span>Construction</span>
        </label>
        <label class="chip" style="--chip-color:#ff3d7f;--chip-glow:rgba(255,61,127,0.2)">
          <input type="checkbox" value="Planned" checked>
          <span class="swatch"></span>
          <span>Planned</span>
        </label>
        <label class="chip" style="--chip-color:#8b5cf6;--chip-glow:rgba(139,92,246,0.2)">
          <input type="checkbox" value="Market hub" checked>
          <span class="swatch"></span>
          <span>Market hubs</span>
        </label>
      </div>

      <div class="layer-row">
        <label class="layer-toggle active">
          <input id="showNuclear" type="checkbox" checked>
          Nuclear
        </label>
        <label class="layer-toggle active">
          <input id="showConnectors" type="checkbox" checked>
          Proximity
        </label>
        <label class="layer-toggle" id="heatToggleLabel">
          <input id="showHeat" type="checkbox">
          Heat
        </label>
      </div>

      <div class="source-line">
        Data-center context: <a href="__TRACKER_URL__" target="_blank" rel="noopener">__TRACKER_SOURCE__</a>
        (__TRACKER_AS_OF__). National tracker reports __TRACKED_OPERATING__ operating and __TRACKED_PLANNED__ planned projects.
        Nuclear source: <a href="__EIA_URL__" target="_blank" rel="noopener">__CACHE_NOTE__EIA-860M __EIA_LABEL__</a>.
      </div>
    </section>

    <!-- This panel is replaced with marker-specific information after a click. -->
    <section class="panel detail-panel" id="detailPanel">
      <div class="detail-title">
        <h2>National View</h2>
        <span class="pill">Landscape</span>
      </div>
      <div class="detail-body">
        Public data-center markers are not a facility census. Market hubs represent metro clusters; project markers use the most precise public location available.
      </div>
      <dl class="detail-list">
        <dt>Tracked</dt><dd>__TRACKED_DC_TOTAL__ U.S. data-center projects</dd>
        <dt>Pipeline</dt><dd>__TRACKED_PLANNED_POWER_GW__ planned public power</dd>
        <dt>Nuclear</dt><dd>__NUCLEAR_MARKER_COUNT__ operating plants in this EIA layer</dd>
      </dl>
    </section>
  </aside>

  <!-- External map libraries: Leaflet, marker clustering, and heatmap support. -->
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
  <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
  <!-- Map behavior, filters, popups, and layer toggles. -->
  <script>
    // These arrays are injected by `script.py` when the HTML is generated.
    // To change the default data, edit DEFAULT_DATA_CENTERS above or pass
    // `--datacenters-csv` when running the generator.
    const dataCenters = __DATA_CENTERS_JSON__;
    const nuclearPlants = __NUCLEAR_PLANTS_JSON__;
    const generatedAt = "__GENERATED_AT__";

    // Marker colors, glow colors, and short labels used by data-center status.
    const statusStyles = {
      "Operating": { color: "#00a6ff", glow: "rgba(0, 166, 255, 0.26)", tag: "OP" },
      "Under construction": { color: "#ffb000", glow: "rgba(255, 176, 0, 0.28)", tag: "UC" },
      "Planned": { color: "#ff3d7f", glow: "rgba(255, 61, 127, 0.26)", tag: "PL" },
      "Market hub": { color: "#8b5cf6", glow: "rgba(139, 92, 246, 0.27)", tag: "HUB" },
      "Other": { color: "#14b8a6", glow: "rgba(20, 184, 166, 0.22)", tag: "DC" }
    };

    // Create the Leaflet map centered on the continental U.S.
    const map = L.map("map", {
      zoomControl: false,
      preferCanvas: true,
      scrollWheelZoom: true
    }).setView([39.5, -98.35], 4);

    // Background map options shown in the layer picker.
    const baseLayers = {
      "Color": L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
      }),
      "Light": L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
      }),
      "Dark": L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
      }),
      "Satellite": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 19,
        attribution: "Tiles &copy; Esri"
      })
    };
    baseLayers.Color.addTo(map);

    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.control.layers(baseLayers, null, { position: "bottomright", collapsed: true }).addTo(map);

    // Cluster data-center markers so dense markets stay readable at low zoom.
    const dcCluster = L.markerClusterGroup({
      showCoverageOnHover: false,
      spiderfyOnMaxZoom: true,
      maxClusterRadius: 44,
      iconCreateFunction: (cluster) => {
        const count = cluster.getChildCount();
        const size = count > 20 ? 58 : count > 9 ? 50 : 44;
        return L.divIcon({
          html: `<div class="cluster-icon" style="--size:${size}px"><b>${count}</b><span>sites</span></div>`,
          className: "cluster-wrap",
          iconSize: L.point(size, size)
        });
      }
    });

    // Overlay groups are toggled by the checkboxes in the control panel.
    const nuclearLayer = L.layerGroup();
    const connectorLayer = L.layerGroup();
    const heatLayer = L.heatLayer(buildHeatPoints(), {
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

    map.addLayer(dcCluster);
    map.addLayer(nuclearLayer);
    map.addLayer(connectorLayer);

    // Cache references to controls that drive filtering and layer visibility.
    const searchInput = document.getElementById("siteSearch");
    const shownCount = document.getElementById("shownCount");
    const showNuclear = document.getElementById("showNuclear");
    const showConnectors = document.getElementById("showConnectors");
    const showHeat = document.getElementById("showHeat");
    const heatToggleLabel = document.getElementById("heatToggleLabel");
    const detailPanel = document.getElementById("detailPanel");

    // Escape untrusted text before inserting it into popups or the detail panel.
    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    // Build a safe external source link and reject non-http(s) URLs.
    function safeExternalLink(url, label) {
      if (!url) return "";
      try {
        const parsed = new URL(url, window.location.href);
        if (!["http:", "https:"].includes(parsed.protocol)) return "";
        return `<a href="${escapeHtml(parsed.href)}" target="_blank" rel="noopener">${escapeHtml(label || "Source")}</a>`;
      } catch {
        return "";
      }
    }

    // Format numeric values for user-facing labels.
    function numberFormat(value, digits = 0) {
      const number = Number(value);
      if (!Number.isFinite(number)) return "";
      return number.toLocaleString(undefined, { maximumFractionDigits: digits });
    }

    // Add the MW suffix only when a real positive capacity exists.
    function mwLabel(value) {
      const number = Number(value);
      if (!Number.isFinite(number) || number <= 0) return "";
      return `${numberFormat(number)} MW`;
    }

    // Size data-center markers by capacity, falling back to landscape weight.
    function markerSize(site, minimum = 24, maximum = 54) {
      const capacity = Number(site.capacity_mw);
      if (Number.isFinite(capacity) && capacity > 0) {
        return Math.max(minimum, Math.min(maximum, Math.round(minimum + Math.sqrt(capacity) * 0.36)));
      }
      const weight = Number(site.landscape_weight);
      if (Number.isFinite(weight) && weight > 0) {
        return Math.max(minimum, Math.min(maximum, Math.round(minimum + weight * 0.82)));
      }
      return minimum;
    }

    // Size nuclear markers by plant nameplate capacity.
    function nuclearSize(plant) {
      const capacity = Number(plant.capacity_mw);
      if (!Number.isFinite(capacity) || capacity <= 0) return 24;
      return Math.max(24, Math.min(46, Math.round(23 + Math.sqrt(capacity) * 0.28)));
    }

    // Create a colored data-center marker icon for Leaflet.
    function dcIcon(site) {
      const style = statusStyles[site.status_group] || statusStyles.Other;
      const size = markerSize(site);
      return L.divIcon({
        className: "",
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
        popupAnchor: [0, -size / 2],
        html: `<div class="map-marker" style="--size:${size}px;--marker:${style.color};--glow:${style.glow}"><span class="marker-label">${style.tag}</span></div>`
      });
    }

    // Create a green nuclear plant marker icon for Leaflet.
    function nuclearIcon(plant) {
      const size = nuclearSize(plant);
      return L.divIcon({
        className: "",
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
        popupAnchor: [0, -size / 2],
        html: `<div class="map-marker nuclear-marker" style="--size:${size}px;--marker:#21c55d;--glow:rgba(33,197,93,0.28)"><span class="marker-label">N</span></div>`
      });
    }

    // Convert popup label/value pairs into a compact table, skipping blanks.
    function popupTable(rows) {
      const tableRows = rows
        .filter(([, value]) => value !== undefined && value !== null && String(value).trim() !== "")
        .map(([label, value]) => `<tr><th>${escapeHtml(label)}</th><td>${value}</td></tr>`)
        .join("");
      return `<table class="popup-table">${tableRows}</table>`;
    }

    // Build the popup HTML for one data-center or market marker.
    function dcPopup(site) {
      const source = safeExternalLink(site.source_url, site.source || "Source");
      const nearest = site.nearest_nuclear_name
        ? `${escapeHtml(site.nearest_nuclear_name)} (${escapeHtml(site.nearest_nuclear_state)}), ${numberFormat(site.nearest_nuclear_distance_mi, 1)} mi`
        : "";
      return `
        <div class="popup-kicker">${escapeHtml(site.status_group)} data-center marker</div>
        <h3 class="popup-title">${escapeHtml(site.name)}</h3>
        ${popupTable([
          ["Developer", escapeHtml(site.developer)],
          ["Category", escapeHtml(site.category)],
          ["Status", escapeHtml(site.status)],
          ["Power", escapeHtml(site.capacity_label || mwLabel(site.capacity_mw))],
          ["Location", escapeHtml(site.location)],
          ["Precision", escapeHtml(site.precision)],
          ["Nearest nuclear", nearest],
          ["Notes", escapeHtml(site.notes)],
          ["Source", source]
        ])}
      `;
    }

    // Build the popup HTML for one nuclear plant marker.
    function nuclearPopup(plant) {
      const source = safeExternalLink(plant.source_url, plant.source);
      return `
        <div class="popup-kicker">Operating nuclear plant</div>
        <h3 class="popup-title">${escapeHtml(plant.name)}</h3>
        ${popupTable([
          ["State", escapeHtml(plant.state)],
          ["County", escapeHtml(plant.county)],
          ["EIA Plant ID", escapeHtml(plant.plant_id)],
          ["Nameplate", escapeHtml(mwLabel(plant.capacity_mw))],
          ["Generator rows", escapeHtml(plant.unit_count)],
          ["Status", escapeHtml(plant.statuses)],
          ["Source", source]
        ])}
      `;
    }

    // Replace the side detail panel with the clicked marker's key facts.
    function updateDetail(site, kind) {
      if (!site) return;
      if (kind === "nuclear") {
        detailPanel.innerHTML = `
          <div class="detail-title">
            <h2>${escapeHtml(site.name)}</h2>
            <span class="pill" style="background:#15803d">Nuclear</span>
          </div>
          <dl class="detail-list">
            <dt>State</dt><dd>${escapeHtml(site.state)}</dd>
            <dt>County</dt><dd>${escapeHtml(site.county)}</dd>
            <dt>Nameplate</dt><dd>${escapeHtml(mwLabel(site.capacity_mw))}</dd>
            <dt>Units</dt><dd>${escapeHtml(site.unit_count)}</dd>
          </dl>
        `;
        return;
      }

      const style = statusStyles[site.status_group] || statusStyles.Other;
      detailPanel.innerHTML = `
        <div class="detail-title">
          <h2>${escapeHtml(site.name)}</h2>
          <span class="pill" style="background:${style.color}">${escapeHtml(site.status_group)}</span>
        </div>
        <div class="detail-body">${escapeHtml(site.notes || site.location || "")}</div>
        <dl class="detail-list">
          <dt>Developer</dt><dd>${escapeHtml(site.developer)}</dd>
          <dt>Power</dt><dd>${escapeHtml(site.capacity_label || mwLabel(site.capacity_mw) || "Market marker")}</dd>
          <dt>Location</dt><dd>${escapeHtml(site.location)}</dd>
          <dt>Nearest N</dt><dd>${escapeHtml(site.nearest_nuclear_name || "")}${site.nearest_nuclear_distance_mi ? `, ${numberFormat(site.nearest_nuclear_distance_mi, 1)} mi` : ""}</dd>
        </dl>
      `;
    }

    // Combine data-center and nuclear coordinates into weighted heatmap points.
    function buildHeatPoints() {
      const dcPoints = dataCenters.map((site) => {
        const capacity = Number(site.capacity_mw);
        const weight = Number(site.landscape_weight);
        const intensity = Number.isFinite(capacity) && capacity > 0
          ? Math.min(1, Math.log10(capacity + 10) / 4.1)
          : Math.min(0.78, Math.max(0.32, weight / 36));
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

    // Read the checked status filters from the control panel.
    function activeStatuses() {
      return new Set(
        Array.from(document.querySelectorAll("#statusFilters input:checked")).map((input) => input.value)
      );
    }

    // Search across the fields users are likely to type: name, company, place, and notes.
    function siteMatchesSearch(site, query) {
      if (!query) return true;
      const haystack = [
        site.name,
        site.developer,
        site.status,
        site.category,
        site.location,
        site.nearest_nuclear_name,
        site.notes
      ].join(" ").toLowerCase();
      return haystack.includes(query);
    }

    // Rebuild the data-center markers and proximity lines from the current filters.
    function refreshDataCenterLayer() {
      const statuses = activeStatuses();
      const query = searchInput.value.trim().toLowerCase();
      dcCluster.clearLayers();
      connectorLayer.clearLayers();

      let shown = 0;
      for (const site of dataCenters) {
        if (!statuses.has(site.status_group) || !siteMatchesSearch(site, query)) {
          continue;
        }
        shown += 1;
        const marker = L.marker([site.latitude, site.longitude], { icon: dcIcon(site), title: site.name })
          .bindPopup(dcPopup(site), { maxWidth: 360 })
          .bindTooltip(`${site.name} - ${site.status_group}`);
        marker.on("click", () => updateDetail(site, "data-center"));
        dcCluster.addLayer(marker);

        if (showConnectors.checked && site.nearest_nuclear_latitude && site.nearest_nuclear_longitude) {
          const style = statusStyles[site.status_group] || statusStyles.Other;
          const line = L.polyline(
            [
              [site.latitude, site.longitude],
              [site.nearest_nuclear_latitude, site.nearest_nuclear_longitude]
            ],
            {
              color: style.color,
              weight: 1.4,
              opacity: 0.36,
              dashArray: "4 7",
              interactive: false
            }
          );
          connectorLayer.addLayer(line);
        }
      }
      shownCount.textContent = shown.toLocaleString();
    }

    // Build all nuclear plant markers once; visibility is toggled separately.
    function buildNuclearLayer() {
      nuclearLayer.clearLayers();
      for (const plant of nuclearPlants) {
        const marker = L.marker([plant.latitude, plant.longitude], { icon: nuclearIcon(plant), title: plant.name })
          .bindPopup(nuclearPopup(plant), { maxWidth: 360 })
          .bindTooltip(`${plant.name} - ${mwLabel(plant.capacity_mw)}`);
        marker.on("click", () => updateDetail(plant, "nuclear"));
        nuclearLayer.addLayer(marker);
      }
    }

    // Keep checkbox state, map layers, and active button styling synchronized.
    function syncLayerToggles() {
      if (showNuclear.checked) {
        if (!map.hasLayer(nuclearLayer)) map.addLayer(nuclearLayer);
      } else if (map.hasLayer(nuclearLayer)) {
        map.removeLayer(nuclearLayer);
      }

      if (showConnectors.checked) {
        if (!map.hasLayer(connectorLayer)) map.addLayer(connectorLayer);
      } else if (map.hasLayer(connectorLayer)) {
        map.removeLayer(connectorLayer);
      }

      if (showHeat.checked) {
        if (!map.hasLayer(heatLayer)) map.addLayer(heatLayer);
        heatToggleLabel.classList.add("active");
      } else {
        if (map.hasLayer(heatLayer)) map.removeLayer(heatLayer);
        heatToggleLabel.classList.remove("active");
      }

      showNuclear.closest(".layer-toggle").classList.toggle("active", showNuclear.checked);
      showConnectors.closest(".layer-toggle").classList.toggle("active", showConnectors.checked);
    }

    // Zoom the initial view to include every marker.
    function fitToData() {
      const bounds = L.latLngBounds([]);
      dataCenters.forEach((site) => bounds.extend([site.latitude, site.longitude]));
      nuclearPlants.forEach((plant) => bounds.extend([plant.latitude, plant.longitude]));
      if (bounds.isValid()) {
        map.fitBounds(bounds.pad(0.08), { maxZoom: 5 });
      }
    }

    // Initial render: build layers, apply default toggles, and frame the data.
    buildNuclearLayer();
    refreshDataCenterLayer();
    syncLayerToggles();
    fitToData();

    // User interactions: search and filter changes rebuild only what needs updating.
    searchInput.addEventListener("input", refreshDataCenterLayer);
    document.querySelectorAll("#statusFilters input").forEach((input) => {
      input.addEventListener("change", refreshDataCenterLayer);
    });
    [showNuclear, showConnectors, showHeat].forEach((input) => {
      input.addEventListener("change", () => {
        refreshDataCenterLayer();
        syncLayerToggles();
      });
    });

    // Show when this static page was generated.
    map.attributionControl.addAttribution(`Generated ${escapeHtml(generatedAt)}`);
  </script>
</body>
</html>
"""

    replacements = {
        "__DATA_CENTERS_JSON__": json_for_html(data_centers),
        "__NUCLEAR_PLANTS_JSON__": json_for_html(nuclear_plants),
        "__GENERATED_AT__": html_escape(generated_at),
        "__DC_MARKER_COUNT__": fmt_number(len(data_centers)),
        "__NUCLEAR_MARKER_COUNT__": fmt_number(len(nuclear_plants)),
        "__NUCLEAR_CAPACITY_GW__": fmt_gw(nuclear_capacity_mw),
        "__DC_CAPACITY_GW__": fmt_gw(dc_capacity_mw),
        "__EIA_LABEL__": html_escape(eia_workbook.label),
        "__EIA_URL__": html_escape(eia_workbook.url),
        "__CACHE_NOTE__": cache_note,
        "__TRACKER_SOURCE__": html_escape(LANDSCAPE_SUMMARY["source"]),
        "__TRACKER_URL__": html_escape(LANDSCAPE_SUMMARY["source_url"]),
        "__TRACKER_AS_OF__": html_escape(LANDSCAPE_SUMMARY["as_of"]),
        "__TRACKED_DC_TOTAL__": fmt_number(LANDSCAPE_SUMMARY["tracked_data_centers"]),
        "__TRACKED_OPERATING__": fmt_number(LANDSCAPE_SUMMARY["operating_data_centers"]),
        "__TRACKED_PLANNED__": fmt_number(LANDSCAPE_SUMMARY["planned_data_centers"]),
        "__TRACKED_PLANNED_POWER_GW__": fmt_gw(LANDSCAPE_SUMMARY["planned_power_mw"]),
    }

    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def save_supporting_files(
    nuclear_plants: list[dict[str, Any]],
    data_centers: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """Write CSV exports next to the HTML so map data can be inspected directly."""

    stem = output_path.with_suffix("")
    write_csv(
        stem.with_name(f"{stem.name}_nuclear_plants.csv"),
        nuclear_plants,
        [
            "plant_id",
            "name",
            "state",
            "county",
            "latitude",
            "longitude",
            "capacity_mw",
            "unit_count",
            "statuses",
            "type",
            "source",
            "source_url",
        ],
    )
    data_center_fields = [
        "name",
        "developer",
        "status",
        "status_group",
        "category",
        "capacity_mw",
        "capacity_label",
        "landscape_weight",
        "location",
        "latitude",
        "longitude",
        "precision",
        "nearest_nuclear_name",
        "nearest_nuclear_state",
        "nearest_nuclear_distance_mi",
        "nearest_nuclear_capacity_mw",
        "source",
        "source_url",
        "notes",
    ]
    write_csv(stem.with_name(f"{stem.name}_data_centers.csv"), data_centers, data_center_fields)
    write_csv(stem.with_name(f"{stem.name}_ai_datacenters.csv"), data_centers, data_center_fields)


def write_csv(path: Path, records: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """Write one CSV file with a stable field order."""

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)


def parse_args() -> argparse.Namespace:
    """Define command-line options for output paths, caching, and custom data."""

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """
            Create an interactive Leaflet HTML map of U.S. data-center activity
            and operating nuclear plants.
            """
        ).strip(),
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
            "Optional CSV for the data-center layer. Required columns: "
            "name, latitude, longitude. Useful columns: developer, status, "
            "category, capacity_mw, capacity_label, landscape_weight, location, "
            "source, source_url, precision, notes."
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Download the latest EIA workbook even if a cached workbook exists.",
    )
    parser.add_argument(
        "--no-supporting-csv",
        action="store_true",
        help="Only write the HTML map; skip supporting CSV exports.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the full build: fetch data, enrich records, render HTML, write CSVs."""

    args = parse_args()
    eia_workbook = download_eia_workbook(args.cache_dir, refresh=args.refresh)
    nuclear_plants = load_nuclear_plants(eia_workbook.path)
    data_centers = load_data_centers(args.datacenters_csv)
    enrich_data_center_proximity(data_centers, nuclear_plants)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        render_map_html(nuclear_plants, data_centers, eia_workbook),
        encoding="utf-8",
    )
    if not args.no_supporting_csv:
        save_supporting_files(nuclear_plants, data_centers, args.output)

    print(f"Map written to: {args.output.resolve()}")
    print(f"Nuclear plants: {len(nuclear_plants)} from EIA-860M {eia_workbook.label}")
    print(f"Data-center / market markers: {len(data_centers)}")
    print(
        "Note: default data-center layer is a curated public landscape layer, "
        "not a complete facility census."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
