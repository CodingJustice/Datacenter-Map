"""
Build the data files used by the U.S. data-center, power, nuclear, and water map.

The current workflow is data-first:
1. Read public data-center facility records from Compute Atlas JSON/API.
2. Read operating and planned power generators from the EIA-860M workbook.
3. Read named water-source reference points from water_sources.csv.
4. Remove market-hub placeholders, compute nearest nuclear/power/water context,
   then write CSV exports plus JSON/GeoJSON files for the browser map.

How to use:
    python script.py
    python script.py --refresh-compute-atlas
    python script.py --compute-atlas-json compute-atlas-data-centers.json
    python script.py --water-sources water_sources.csv
    python script.py --output-dir data

This script uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import posixpath
import re
import sqlite3
import sys
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from zipfile import ZipFile


COMPUTE_ATLAS_DATA_CENTERS_API = "https://www.compute-atlas.com/api/facilities?type=data_center"
COMPUTE_ATLAS_ALL_FACILITIES_API = "https://www.compute-atlas.com/api/facilities"
COMPUTE_ATLAS_URL = "https://www.compute-atlas.com/"
EIA_860M_PAGE = "https://www.eia.gov/electricity/data/eia860m/"
EIA_860M_PAGE_ALT = "https://www.eia.gov/electricity/data/eia860m/index.php"
USGS_NHD_URL = "https://www.usgs.gov/national-hydrography/national-hydrography-dataset"
NATURAL_EARTH_URL = "https://www.naturalearthdata.com/"

PROJECT_ROOT = Path(__file__).resolve().parent
MAX_API_RESPONSE_BYTES = 35 * 1024 * 1024
MAX_JSON_FILE_BYTES = 35 * 1024 * 1024
MAX_CSV_FILE_BYTES = 15 * 1024 * 1024
MAX_XLSX_FILE_BYTES = 150 * 1024 * 1024
MAX_XLSX_MEMBER_BYTES = 80 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_API_FACILITY_RECORDS = 100_000
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")

ALLOWED_UPSTREAM_HOSTS = {
    "compute-atlas.com",
    "www.compute-atlas.com",
    "eia.gov",
    "www.eia.gov",
}

DATA_CENTER_FIELDS = [
    "id",
    "name",
    "developer",
    "status",
    "status_group",
    "category",
    "facility_type",
    "ai_classification",
    "confidence",
    "capacity_mw",
    "operational_capacity_mw",
    "planned_capacity_mw",
    "capacity_label",
    "landscape_weight",
    "location",
    "city",
    "county",
    "state",
    "latitude",
    "longitude",
    "precision",
    "powered_by",
    "energy_source",
    "utility",
    "onsite_generation_mw",
    "water_cooling_type",
    "water_reported_mgd",
    "water_notes",
    "community_status",
    "investment_usd",
    "land_acres",
    "jobs_construction",
    "jobs_permanent",
    "nearest_nuclear_name",
    "nearest_nuclear_state",
    "nearest_nuclear_distance_mi",
    "nearest_nuclear_capacity_mw",
    "nearest_nuclear_latitude",
    "nearest_nuclear_longitude",
    "nearest_power_name",
    "nearest_power_state",
    "nearest_power_distance_mi",
    "nearest_power_capacity_mw",
    "nearest_power_source",
    "nearest_power_latitude",
    "nearest_power_longitude",
    "nearest_water_name",
    "nearest_water_type",
    "nearest_water_distance_mi",
    "nearest_water_latitude",
    "nearest_water_longitude",
    "source",
    "source_url",
    "source_count",
    "notes",
]

POWER_FIELDS = [
    "plant_id",
    "name",
    "operator",
    "state",
    "county",
    "latitude",
    "longitude",
    "capacity_mw",
    "status_group",
    "statuses",
    "technologies",
    "energy_sources",
    "type",
    "is_nuclear",
    "source",
    "source_url",
    "notes",
]

NUCLEAR_FIELDS = POWER_FIELDS

WATER_FIELDS = [
    "name",
    "type",
    "state",
    "latitude",
    "longitude",
    "source",
    "source_url",
    "notes",
]

NUMERIC_FIELDS = {
    "capacity_mw",
    "operational_capacity_mw",
    "planned_capacity_mw",
    "landscape_weight",
    "latitude",
    "longitude",
    "onsite_generation_mw",
    "water_reported_mgd",
    "investment_usd",
    "land_acres",
    "jobs_construction",
    "jobs_permanent",
    "nearest_nuclear_distance_mi",
    "nearest_nuclear_capacity_mw",
    "nearest_nuclear_latitude",
    "nearest_nuclear_longitude",
    "nearest_power_distance_mi",
    "nearest_power_capacity_mw",
    "nearest_power_latitude",
    "nearest_power_longitude",
    "nearest_water_distance_mi",
    "nearest_water_latitude",
    "nearest_water_longitude",
    "unit_count",
    "source_count",
}

SQLITE_TABLES = {
    "data_centers": DATA_CENTER_FIELDS,
    "power_plants": POWER_FIELDS,
    "nuclear_plants": NUCLEAR_FIELDS,
    "water_sources": WATER_FIELDS,
}

SQLITE_INDEXES = {
    "data_centers": [
        ("state", "status_group"),
        ("latitude", "longitude"),
        ("nearest_nuclear_distance_mi",),
        ("nearest_power_distance_mi",),
        ("nearest_water_distance_mi",),
    ],
    "power_plants": [
        ("state", "status_group"),
        ("latitude", "longitude"),
        ("energy_sources",),
    ],
    "nuclear_plants": [
        ("state", "status_group"),
        ("latitude", "longitude"),
    ],
    "water_sources": [
        ("state",),
        ("latitude", "longitude"),
    ],
}

GEOJSON_COORDINATE_DECIMALS = {
    "data-center": 4,
    "nuclear": 4,
    "power": 3,
    "water": 4,
    "data-center-hub": 3,
}

DETAIL_SHARD_COUNTS = {
    "data-centers": 8,
    "power-plants": 64,
    "nuclear-plants": 1,
    "water-sources": 1,
}

DATA_CENTER_SEARCH_FIELDS = [
    "name",
    "developer",
    "status",
    "status_group",
    "category",
    "location",
    "city",
    "county",
    "state",
    "powered_by",
    "energy_source",
    "utility",
    "water_cooling_type",
    "nearest_nuclear_name",
    "nearest_power_name",
    "nearest_water_name",
]

XLSX_MAIN_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
XLSX_REL_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
XLSX_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def project_path(path: Path) -> Path:
    candidate = path if path.is_absolute() else PROJECT_ROOT / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("Input and output paths must stay inside the project directory.") from exc
    return resolved


def resolve_output_dir(path: Path) -> Path:
    resolved = project_path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def resolve_output_file(path: Path, allowed_suffixes: set[str], label: str) -> Path:
    resolved = project_path(path)
    if resolved.suffix.lower() not in allowed_suffixes:
        raise ValueError(f"{label} must use one of these suffixes: {', '.join(sorted(allowed_suffixes))}.")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def validate_existing_file(path: Path, allowed_suffixes: set[str], max_bytes: int, label: str) -> Path:
    resolved = project_path(path)
    if resolved.suffix.lower() not in allowed_suffixes:
        raise ValueError(f"{label} must use one of these suffixes: {', '.join(sorted(allowed_suffixes))}.")
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} not found.")
    size = resolved.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{label} exceeds the allowed size limit.")
    return resolved


def validate_upstream_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in ALLOWED_UPSTREAM_HOSTS:
        raise ValueError("Only approved HTTPS data-source URLs are allowed.")


def validate_zip_members(workbook: ZipFile) -> None:
    total_uncompressed = 0
    for member in workbook.infolist():
        member_path = PurePosixPath(member.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError("Workbook contains an invalid internal file path.")
        if member.file_size > MAX_XLSX_MEMBER_BYTES:
            raise ValueError("Workbook contains an oversized member.")
        total_uncompressed += member.file_size
        if total_uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
            raise ValueError("Workbook expands beyond the allowed size limit.")


def load_json_payload(path: Path, label: str) -> dict[str, Any]:
    validated = validate_existing_file(path, {".json"}, MAX_JSON_FILE_BYTES, label)
    try:
        payload = json.loads(validated.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object.")
    return payload


def facility_list(payload: dict[str, Any], label: str) -> list[dict[str, Any]]:
    facilities = payload.get("facilities", [])
    if not isinstance(facilities, list):
        raise ValueError(f"{label} payload did not contain a facilities list.")
    if len(facilities) > MAX_API_FACILITY_RECORDS:
        raise ValueError(f"{label} payload contains too many facility records.")
    if not all(isinstance(item, dict) for item in facilities):
        raise ValueError(f"{label} payload contains invalid facility records.")
    return facilities


def fetch_url(
    url: str,
    timeout: int = 60,
    binary: bool = False,
    max_bytes: int = MAX_API_RESPONSE_BYTES,
) -> str | bytes:
    validate_upstream_url(url)
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; datacenter-nuclear-map/3.0; "
                "+https://www.incompas.org/)"
            )
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    response_size = int(content_length)
                except ValueError:
                    raise ValueError("Upstream response reported an invalid size.") from None
                if response_size > max_bytes:
                    raise ValueError("Upstream response exceeds the allowed size limit.")
            payload = response.read(max_bytes + 1)
    except URLError:
        raise

    if len(payload) > max_bytes:
        raise ValueError("Upstream response exceeds the allowed size limit.")

    return payload if binary else payload.decode("utf-8", errors="replace")


def find_latest_eia860m_workbook() -> tuple[str, str]:
    page = fetch_url(EIA_860M_PAGE_ALT, timeout=30, binary=False)
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
    return label, urljoin(EIA_860M_PAGE_ALT, href)


def label_from_workbook_filename(path: Path) -> str:
    match = re.search(r"([a-z]+)_generator(\d{4})", path.name, flags=re.IGNORECASE)
    if not match:
        return path.stem
    month, year = match.groups()
    return f"{month.title()} {year}"


def find_newest_cached_workbook(cache_dir: Path) -> Path | None:
    workbooks = sorted(
        cache_dir.glob("*_generator*.xlsx"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    return workbooks[0] if workbooks else None


def download_eia_workbook(cache_dir: Path, refresh: bool = False) -> tuple[str, str, Path, bool]:
    cache_dir = resolve_output_dir(cache_dir)
    try:
        label, url = find_latest_eia860m_workbook()
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
        cached = find_newest_cached_workbook(cache_dir)
        if cached:
            validate_existing_file(cached, {".xlsx"}, MAX_XLSX_FILE_BYTES, "Cached EIA-860M workbook")
            cached_url = urljoin(EIA_860M_PAGE_ALT, f"xls/{cached.name}")
            warnings.warn(
                f"Could not reach EIA-860M ({exc}). Using cached workbook {cached.name}.",
                RuntimeWarning,
                stacklevel=2,
            )
            return label_from_workbook_filename(cached), cached_url, cached, True
        raise

    filename = Path(urlparse(url).path).name
    workbook_path = resolve_output_file(cache_dir / filename, {".xlsx"}, "EIA-860M workbook")
    if refresh or not workbook_path.exists():
        payload = fetch_url(url, timeout=120, binary=True, max_bytes=MAX_XLSX_FILE_BYTES)
        assert isinstance(payload, bytes)
        workbook_path.write_bytes(payload)

    validate_existing_file(workbook_path, {".xlsx"}, MAX_XLSX_FILE_BYTES, "EIA-860M workbook")
    return label, url, workbook_path, False


def read_shared_strings(workbook: ZipFile) -> list[str]:
    try:
        root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(item.itertext()) for item in root.findall("main:si", XLSX_MAIN_NS)]


def workbook_sheet_paths(workbook: ZipFile) -> dict[str, str]:
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
        paths[name] = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
    return paths


def excel_column_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref)
    if not match:
        raise ValueError(f"Invalid Excel cell reference: {cell_ref}")
    index = 0
    for letter in match.group(1):
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index - 1


def xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(cell.itertext())

    value = cell.find("main:v", XLSX_MAIN_NS)
    if value is None or value.text is None:
        return ""

    text = value.text
    return shared_strings[int(text)] if cell_type == "s" else text


def read_xlsx_sheet(path: Path, sheet_name: str, header_row: int = 3) -> list[dict[str, str]]:
    path = validate_existing_file(path, {".xlsx"}, MAX_XLSX_FILE_BYTES, "EIA-860M workbook")
    with ZipFile(path) as workbook:
        validate_zip_members(workbook)
        shared_strings = read_shared_strings(workbook)
        sheet_paths = workbook_sheet_paths(workbook)
        if sheet_name not in sheet_paths:
            return []

        sheet_root = ET.fromstring(workbook.read(sheet_paths[sheet_name]))
        headers: list[str] | None = None
        rows: list[dict[str, str]] = []

        for row in sheet_root.findall(".//main:sheetData/main:row", XLSX_MAIN_NS):
            row_number = int(row.attrib.get("r", "0"))
            values: dict[int, str] = {}
            for cell in row.findall("main:c", XLSX_MAIN_NS):
                values[excel_column_index(cell.attrib["r"])] = xlsx_cell_value(cell, shared_strings)

            if row_number == header_row:
                max_index = max(values.keys()) if values else -1
                headers = [clean_text(values.get(i, "")) for i in range(max_index + 1)]
                continue

            if row_number <= header_row or headers is None or not values:
                continue

            max_index = min(len(headers), max(values.keys()) + 1)
            record = {
                headers[i]: values.get(i, "")
                for i in range(max_index)
                if i < len(headers) and headers[i]
            }
            if any(clean_text(value) for value in record.values()):
                rows.append(record)

    return rows


def clean_text(value: Any, max_length: int = 4000) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    return text.strip()[:max_length]


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = clean_text(value).replace(",", "").rstrip("+")
    if not text or text.lower() in {"na", "n/a", "none", "null", "-"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def fmt_number(value: float | int) -> str:
    return f"{value:,.0f}"


def status_label(status: str) -> str:
    labels = {
        "operational": "Operating",
        "under_construction": "Under construction",
        "permitted": "Permitted",
        "proposed": "Proposed",
        "cancelled": "Cancelled",
    }
    return labels.get(status, clean_text(status).replace("_", " ").title() or "Other")


def status_group(status: str) -> str:
    label = status_label(status)
    return label if label in {"Operating", "Under construction", "Permitted", "Proposed", "Cancelled"} else "Other"


def capacity_label(operational: float | None, planned: float | None) -> str:
    if operational and planned and planned != operational:
        return f"{fmt_number(operational)} MW operating / {fmt_number(planned)} MW planned"
    if planned:
        return f"{fmt_number(planned)} MW planned"
    if operational:
        return f"{fmt_number(operational)} MW operating"
    return ""


def first_source(facility: dict[str, Any]) -> tuple[str, str, int]:
    sources = facility.get("sources") or []
    if not isinstance(sources, list):
        return "", "", 0
    for source in sources:
        if isinstance(source, dict):
            url = clean_text(source.get("url"))
            label = clean_text(source.get("label") or source.get("publisher") or "Source")
            if url:
                return label, url, len(sources)
    return "", "", len(sources)


def load_compute_atlas_records(path: Path, refresh: bool = False) -> list[dict[str, Any]]:
    path = resolve_output_file(path, {".json"}, "Compute Atlas data-center JSON")
    if refresh or not path.exists():
        payload = fetch_url(
            COMPUTE_ATLAS_DATA_CENTERS_API,
            timeout=120,
            binary=False,
            max_bytes=MAX_JSON_FILE_BYTES,
        )
        assert isinstance(payload, str)
        path.write_text(payload, encoding="utf-8")
    return facility_list(load_json_payload(path, "Compute Atlas data-center JSON"), "Compute Atlas data-center")


def load_compute_atlas_power_records(path: Path, refresh: bool = False) -> list[dict[str, Any]]:
    path = resolve_output_file(path, {".json"}, "Compute Atlas facilities JSON")
    if refresh or not path.exists():
        payload = fetch_url(
            COMPUTE_ATLAS_ALL_FACILITIES_API,
            timeout=120,
            binary=False,
            max_bytes=MAX_JSON_FILE_BYTES,
        )
        assert isinstance(payload, str)
        path.write_text(payload, encoding="utf-8")
    facilities = facility_list(load_json_payload(path, "Compute Atlas facilities JSON"), "Compute Atlas facilities")
    return [
        item for item in facilities
        if isinstance(item, dict) and item.get("facilityType") == "power_generation"
    ]


def normalize_data_centers(facilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for facility in facilities:
        if facility.get("facilityType") != "data_center":
            continue

        location = facility.get("location") or {}
        latitude = safe_float(location.get("lat"))
        longitude = safe_float(location.get("lon"))
        if latitude is None or longitude is None:
            continue

        capacity = facility.get("capacityMw") or {}
        operational = safe_float(capacity.get("operational"))
        planned = safe_float(capacity.get("planned"))
        shown_capacity = operational or planned
        water = facility.get("water") or {}
        energy = facility.get("energy") or {}
        community = facility.get("community") or {}
        jobs = facility.get("jobs") or {}
        source_label, source_url, source_count = first_source(facility)
        city = clean_text(location.get("city"))
        county = clean_text(location.get("county"))
        state = clean_text(location.get("state"))
        location_text = ", ".join(part for part in [city, county, state] if part)

        records.append({
            "id": clean_text(facility.get("id"), 160),
            "name": clean_text(facility.get("name")),
            "developer": clean_text(facility.get("operator")),
            "status": status_label(clean_text(facility.get("status"))),
            "status_group": status_group(clean_text(facility.get("status"))),
            "category": clean_text(facility.get("aiClassification") or "Data center"),
            "facility_type": clean_text(facility.get("facilityType")),
            "ai_classification": clean_text(facility.get("aiClassification")),
            "confidence": clean_text(facility.get("confidence")),
            "capacity_mw": shown_capacity,
            "operational_capacity_mw": operational,
            "planned_capacity_mw": planned,
            "capacity_label": capacity_label(operational, planned),
            "landscape_weight": safe_float(facility.get("landAcres")) or shown_capacity or 8,
            "location": location_text,
            "city": city,
            "county": county,
            "state": state,
            "latitude": latitude,
            "longitude": longitude,
            "precision": clean_text(location.get("precision")),
            "powered_by": clean_text(facility.get("poweredBy")),
            "energy_source": clean_text(energy.get("source")),
            "utility": clean_text(energy.get("utility")),
            "onsite_generation_mw": safe_float(energy.get("onSiteGenerationMw")),
            "water_cooling_type": clean_text(water.get("coolingType")),
            "water_reported_mgd": safe_float(water.get("reportedMgd")),
            "water_notes": clean_text(water.get("notes")),
            "community_status": clean_text(community.get("status")),
            "investment_usd": safe_float(facility.get("investmentUsd")),
            "land_acres": safe_float(facility.get("landAcres")),
            "jobs_construction": safe_float(jobs.get("construction")),
            "jobs_permanent": safe_float(jobs.get("permanent")),
            "source": source_label or "Compute Atlas",
            "source_url": source_url or COMPUTE_ATLAS_URL,
            "source_count": source_count,
            "notes": clean_text(facility.get("notes")),
        })

    return sorted(records, key=lambda item: (str(item["state"]), str(item["name"])))


def eia_status_group(statuses: Iterable[str], sheet_group: str) -> str:
    combined = " ".join(statuses).lower()
    if "construction" in combined or "(v)" in combined or "(ts)" in combined:
        return "Under construction"
    if sheet_group == "Planned":
        return "Planned"
    return "Operating"


def load_eia_power_plants(workbook_path: Path, eia_label: str, eia_url: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for sheet_name, sheet_group in [("Operating", "Operating"), ("Planned", "Planned")]:
        for row in read_xlsx_sheet(workbook_path, sheet_name=sheet_name, header_row=3):
            latitude = safe_float(row.get("Latitude"))
            longitude = safe_float(row.get("Longitude"))
            if latitude is None or longitude is None:
                continue

            plant_id = clean_text(row.get("Plant ID"))
            name = clean_text(row.get("Plant Name"))
            state = clean_text(row.get("Plant State"))
            county = clean_text(row.get("County"))
            key = (plant_id, name, state, county, latitude, longitude, sheet_group)
            plant = grouped.setdefault(key, {
                "plant_id": plant_id,
                "name": name,
                "operator": clean_text(row.get("Entity Name")),
                "state": state,
                "county": county,
                "latitude": latitude,
                "longitude": longitude,
                "capacity_mw": 0.0,
                "status_group": sheet_group,
                "statuses_set": set(),
                "technologies_set": set(),
                "energy_sources_set": set(),
                "type": "Power plant",
                "is_nuclear": False,
                "source": f"EIA-860M {eia_label}",
                "source_url": eia_url,
                "notes": "",
            })

            plant["capacity_mw"] += safe_float(row.get("Nameplate Capacity (MW)")) or 0
            status = clean_text(row.get("Status"))
            technology = clean_text(row.get("Technology"))
            energy_source = clean_text(row.get("Energy Source Code"))
            if status:
                plant["statuses_set"].add(status)
            if technology:
                plant["technologies_set"].add(technology)
            if energy_source:
                plant["energy_sources_set"].add(energy_source)
            if energy_source == "NUC" or "nuclear" in technology.lower():
                plant["is_nuclear"] = True

    plants: list[dict[str, Any]] = []
    for plant in grouped.values():
        statuses = sorted(plant.pop("statuses_set"))
        technologies = sorted(plant.pop("technologies_set"))
        energy_sources = sorted(plant.pop("energy_sources_set"))
        plant["capacity_mw"] = round(float(plant["capacity_mw"]), 1)
        plant["statuses"] = "; ".join(statuses)
        plant["technologies"] = "; ".join(technologies)
        plant["energy_sources"] = "; ".join(energy_sources)
        plant["status_group"] = eia_status_group(statuses, plant["status_group"])
        if plant["is_nuclear"]:
            plant["type"] = "Nuclear power plant"
        plants.append(plant)

    return plants


def normalize_compute_power_records(facilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for facility in facilities:
        location = facility.get("location") or {}
        latitude = safe_float(location.get("lat"))
        longitude = safe_float(location.get("lon"))
        if latitude is None or longitude is None:
            continue
        generation = facility.get("generation") or {}
        technology = clean_text(generation.get("technology"))
        capacity = safe_float((facility.get("capacityMw") or {}).get("planned"))
        source_label, source_url, _ = first_source(facility)
        records.append({
            "plant_id": clean_text(facility.get("id")),
            "name": clean_text(facility.get("name")),
            "operator": clean_text(facility.get("operator")),
            "state": clean_text(location.get("state")),
            "county": clean_text(location.get("county")),
            "latitude": latitude,
            "longitude": longitude,
            "capacity_mw": capacity,
            "status_group": status_group(clean_text(facility.get("status"))),
            "statuses": status_label(clean_text(facility.get("status"))),
            "technologies": technology.replace("_", " ").title(),
            "energy_sources": technology,
            "type": "Nuclear power plant" if technology.startswith("nuclear") else "Power plant",
            "is_nuclear": technology.startswith("nuclear"),
            "source": source_label or "Compute Atlas",
            "source_url": source_url or COMPUTE_ATLAS_URL,
            "notes": clean_text(generation.get("notes") or facility.get("notes")),
        })
    return records


def load_water_sources(path: Path) -> list[dict[str, Any]]:
    if not project_path(path).exists():
        raise FileNotFoundError(
            f"Water source CSV not found: {path}. Create one with columns {WATER_FIELDS}."
        )
    path = validate_existing_file(path, {".csv"}, MAX_CSV_FILE_BYTES, "Water source CSV")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Water source CSV must include a header row.")
        missing = [field for field in WATER_FIELDS if field not in reader.fieldnames]
        if missing:
            raise ValueError(f"Water source CSV is missing required fields: {', '.join(missing)}.")
        records = list(reader)
    water_sources: list[dict[str, Any]] = []
    for record in records:
        latitude = safe_float(record.get("latitude"))
        longitude = safe_float(record.get("longitude"))
        if latitude is None or longitude is None:
            continue
        water_sources.append({
            "name": clean_text(record.get("name")),
            "type": clean_text(record.get("type")),
            "state": clean_text(record.get("state")),
            "latitude": latitude,
            "longitude": longitude,
            "source": clean_text(record.get("source") or "Natural Earth / USGS context"),
            "source_url": clean_text(record.get("source_url") or NATURAL_EARTH_URL),
            "notes": clean_text(record.get("notes")),
        })
    return water_sources


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
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


def nearest_record(site: dict[str, Any], records: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    nearest: dict[str, Any] | None = None
    nearest_distance = float("inf")
    for record in records:
        distance = haversine_miles(
            float(site["latitude"]),
            float(site["longitude"]),
            float(record["latitude"]),
            float(record["longitude"]),
        )
        if distance < nearest_distance:
            nearest_distance = distance
            nearest = record
    return nearest, nearest_distance


def enrich_data_centers(
    data_centers: list[dict[str, Any]],
    nuclear_plants: list[dict[str, Any]],
    power_plants: list[dict[str, Any]],
    water_sources: list[dict[str, Any]],
) -> None:
    for site in data_centers:
        nearest_nuclear, nuclear_distance = nearest_record(site, nuclear_plants)
        if nearest_nuclear:
            site.update({
                "nearest_nuclear_name": nearest_nuclear["name"],
                "nearest_nuclear_state": nearest_nuclear.get("state", ""),
                "nearest_nuclear_capacity_mw": nearest_nuclear.get("capacity_mw"),
                "nearest_nuclear_distance_mi": round(nuclear_distance, 1),
                "nearest_nuclear_latitude": nearest_nuclear["latitude"],
                "nearest_nuclear_longitude": nearest_nuclear["longitude"],
            })

        nearest_power, power_distance = nearest_record(site, power_plants)
        if nearest_power:
            site.update({
                "nearest_power_name": nearest_power["name"],
                "nearest_power_state": nearest_power.get("state", ""),
                "nearest_power_capacity_mw": nearest_power.get("capacity_mw"),
                "nearest_power_source": nearest_power.get("energy_sources", ""),
                "nearest_power_distance_mi": round(power_distance, 1),
                "nearest_power_latitude": nearest_power["latitude"],
                "nearest_power_longitude": nearest_power["longitude"],
            })

        nearest_water, water_distance = nearest_record(site, water_sources)
        if nearest_water:
            site.update({
                "nearest_water_name": nearest_water["name"],
                "nearest_water_type": nearest_water.get("type", ""),
                "nearest_water_distance_mi": round(water_distance, 1),
                "nearest_water_latitude": nearest_water["latitude"],
                "nearest_water_longitude": nearest_water["longitude"],
            })


def total_capacity(records: Iterable[dict[str, Any]]) -> float:
    return sum(safe_float(record.get("capacity_mw")) or 0 for record in records)


def csv_safe_value(value: Any, field: str) -> Any:
    if field in NUMERIC_FIELDS or isinstance(value, (int, float, bool)) or value is None:
        return value
    text = clean_text(value)
    return f"'{text}" if text.startswith(CSV_FORMULA_PREFIXES) else text


def csv_safe_record(record: dict[str, Any], fieldnames: list[str]) -> dict[str, Any]:
    return {field: csv_safe_value(record.get(field), field) for field in fieldnames}


def write_csv(path: Path, records: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path = resolve_output_file(path, {".csv"}, "CSV export")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(csv_safe_record(record, fieldnames))


def quote_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", identifier):
        raise ValueError("Invalid SQLite identifier.")
    return f'"{identifier}"'


def sqlite_column_definition(field: str) -> str:
    if field == "is_nuclear":
        sql_type = "INTEGER"
    elif field in NUMERIC_FIELDS:
        sql_type = "REAL"
    else:
        sql_type = "TEXT"
    return f"{quote_identifier(field)} {sql_type}"


def sqlite_value(record: dict[str, Any], field: str) -> Any:
    if field == "is_nuclear":
        value = record.get(field)
        if isinstance(value, bool):
            return 1 if value else 0
        return 1 if clean_text(value).lower() in {"true", "1", "yes"} else 0
    if field in NUMERIC_FIELDS:
        return safe_float(record.get(field))
    return clean_text(record.get(field))


def write_sqlite_table(
    connection: sqlite3.Connection,
    table: str,
    records: list[dict[str, Any]],
    fields: list[str],
) -> None:
    table_name = quote_identifier(table)
    columns = ", ".join(sqlite_column_definition(field) for field in fields)
    connection.execute(f"DROP TABLE IF EXISTS {table_name}")
    connection.execute(f"CREATE TABLE {table_name} ({columns})")

    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(quote_identifier(field) for field in fields)
    rows = [
        tuple(sqlite_value(record, field) for field in fields)
        for record in records
    ]
    connection.executemany(
        f"INSERT INTO {table_name} ({quoted_fields}) VALUES ({placeholders})",
        rows,
    )


def create_sqlite_indexes(connection: sqlite3.Connection) -> None:
    for table, indexes in SQLITE_INDEXES.items():
        for columns in indexes:
            index_name = quote_identifier(f"idx_{table}_{'_'.join(columns)}")
            table_name = quote_identifier(table)
            quoted_columns = ", ".join(quote_identifier(column) for column in columns)
            connection.execute(f"CREATE INDEX {index_name} ON {table_name} ({quoted_columns})")


def write_sqlite_database(
    path: Path,
    data_centers: list[dict[str, Any]],
    nuclear_plants: list[dict[str, Any]],
    power_plants: list[dict[str, Any]],
    water_sources: list[dict[str, Any]],
    generated_at: str,
    input_files: list[dict[str, Any]],
    output_files: list[dict[str, Any]],
) -> Path:
    path = resolve_output_file(path, {".db", ".sqlite", ".sqlite3"}, "SQLite test database")
    temp_path = path.with_name(f"{path.name}.tmp")
    if temp_path.exists():
        temp_path.unlink()

    table_records = {
        "data_centers": data_centers,
        "power_plants": power_plants,
        "nuclear_plants": nuclear_plants,
        "water_sources": water_sources,
    }

    connection = sqlite3.connect(temp_path)
    try:
        with connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA user_version = 1")
            for table, fields in SQLITE_TABLES.items():
                write_sqlite_table(connection, table, table_records[table], fields)

            create_sqlite_indexes(connection)
            connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.executemany(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                [
                    ("generated_at", generated_at),
                    ("format", "sqlite-test-backend"),
                    ("data_centers", str(len(data_centers))),
                    ("power_plants", str(len(power_plants))),
                    ("nuclear_plants", str(len(nuclear_plants))),
                    ("water_sources", str(len(water_sources))),
                ],
            )
            connection.execute(
                "CREATE TABLE change_records (role TEXT NOT NULL, label TEXT NOT NULL, file TEXT NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO change_records (role, label, file, bytes, sha256) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        role,
                        clean_text(item["label"]),
                        clean_text(item["file"]),
                        int(item["bytes"]),
                        clean_text(item["sha256"]),
                    )
                    for role, files in [("input", input_files), ("output", output_files)]
                    for item in files
                ],
            )
            connection.execute("ANALYZE")
    finally:
        connection.close()

    temp_path.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def value_for_json(value: Any, field: str) -> Any:
    if field in NUMERIC_FIELDS:
        number = safe_float(value)
        return number if number is not None else None
    if isinstance(value, bool):
        return value
    return clean_text(value)


def records_for_json(records: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for record in records:
        item: dict[str, Any] = {}
        for field in fields:
            value = value_for_json(record.get(field), field)
            if value not in ("", None):
                item[field] = value
            elif field in {"latitude", "longitude"}:
                item[field] = value
        compact.append(item)
    return compact


def rounded_coordinate(value: float, layer: str) -> float:
    return round(value, GEOJSON_COORDINATE_DECIMALS.get(layer, 4))


def compact_search_text(record: dict[str, Any]) -> str:
    return " ".join(
        clean_text(record.get(field), 160)
        for field in DATA_CENTER_SEARCH_FIELDS
        if clean_text(record.get(field), 160)
    ).lower()


def stable_hash(value: str, length: int = 12) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def record_key(record: dict[str, Any], layer: str) -> str:
    if layer == "data-center":
        identifier = clean_text(record.get("id"), 120)
        if identifier:
            return identifier
    elif layer in {"power", "nuclear"}:
        plant_id = clean_text(record.get("plant_id"), 80)
        basis = "|".join(
            clean_text(record.get(field), 160)
            for field in ["plant_id", "name", "state", "latitude", "longitude", "status_group"]
        )
        return f"{plant_id or 'plant'}-{stable_hash(basis, 8)}"

    basis = "|".join(
        clean_text(record.get(field), 160)
        for field in ["name", "type", "state", "latitude", "longitude"]
    )
    return f"{layer}-{stable_hash(basis, 10)}"


def detail_file_for_key(stem: str, key: str) -> str:
    shard_count = DETAIL_SHARD_COUNTS[stem]
    if shard_count == 1:
        return f"data/details/{stem}.json"
    bucket = int(stable_hash(key, 8), 16) % shard_count
    return f"data/details/{stem}-{bucket:02d}.json"


def common_marker_properties(record: dict[str, Any], layer: str, stem: str) -> dict[str, Any]:
    key = record_key(record, layer)
    return {
        "key": key,
        "layer": layer,
        "name": clean_text(record.get("name")),
        "state": clean_text(record.get("state"), 10),
        "detail_file": detail_file_for_key(stem, key),
    }


def marker_properties(record: dict[str, Any], layer: str, stem: str) -> dict[str, Any]:
    properties = common_marker_properties(record, layer, stem)
    if layer == "data-center":
        properties.update({
            "id": clean_text(record.get("id"), 160),
            "status": clean_text(record.get("status")),
            "status_group": clean_text(record.get("status_group")),
            "capacity_mw": value_for_json(record.get("capacity_mw"), "capacity_mw"),
            "capacity_label": clean_text(record.get("capacity_label")),
            "landscape_weight": value_for_json(record.get("landscape_weight"), "landscape_weight"),
            "search": compact_search_text(record),
        })
        for kind in ["nuclear", "power", "water"]:
            lat_field = f"nearest_{kind}_latitude"
            lon_field = f"nearest_{kind}_longitude"
            latitude = safe_float(record.get(lat_field))
            longitude = safe_float(record.get(lon_field))
            if latitude is not None and longitude is not None:
                properties[lat_field] = rounded_coordinate(latitude, "data-center")
                properties[lon_field] = rounded_coordinate(longitude, "data-center")
        return {key: value for key, value in properties.items() if value not in ("", None)}

    if layer in {"power", "nuclear"}:
        properties.update({
            "plant_id": clean_text(record.get("plant_id"), 80),
            "capacity_mw": value_for_json(record.get("capacity_mw"), "capacity_mw"),
            "status_group": clean_text(record.get("status_group")),
            "energy_sources": clean_text(record.get("energy_sources"), 80),
            "type": clean_text(record.get("type") or "Power plant"),
            "is_nuclear": bool(record.get("is_nuclear")),
        })
        return {key: value for key, value in properties.items() if value not in ("", None)}

    properties.update({
        "type": clean_text(record.get("type")),
    })
    return {key: value for key, value in properties.items() if value not in ("", None)}


def feature_for_record(record: dict[str, Any], layer: str, stem: str) -> dict[str, Any] | None:
    latitude = safe_float(record.get("latitude"))
    longitude = safe_float(record.get("longitude"))
    if latitude is None or longitude is None:
        return None
    if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
        return None

    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [
                rounded_coordinate(longitude, layer),
                rounded_coordinate(latitude, layer),
            ],
        },
        "properties": marker_properties(record, layer, stem),
    }


def feature_collection(records: list[dict[str, Any]], layer: str, stem: str) -> dict[str, Any]:
    features = [
        feature
        for feature in (feature_for_record(record, layer, stem) for record in records)
        if feature is not None
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def data_center_hub_collection(records: list[dict[str, Any]]) -> dict[str, Any]:
    grid_size = 0.85
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for record in records:
        latitude = safe_float(record.get("latitude"))
        longitude = safe_float(record.get("longitude"))
        if latitude is None or longitude is None:
            continue
        key = (math.floor(latitude / grid_size), math.floor(longitude / grid_size))
        grouped.setdefault(key, []).append(record)

    features: list[dict[str, Any]] = []
    for (grid_lat, grid_lon), items in sorted(grouped.items()):
        count = len(items)
        latitudes = [float(item["latitude"]) for item in items]
        longitudes = [float(item["longitude"]) for item in items]
        capacity = round(total_capacity(items), 1)
        states = sorted({clean_text(item.get("state"), 10) for item in items if clean_text(item.get("state"), 10)})
        status_counts: dict[str, int] = {}
        for item in items:
            status = clean_text(item.get("status_group")) or "Other"
            status_counts[status] = status_counts.get(status, 0) + 1
        hub_id = f"hub-{grid_lat}-{grid_lon}"
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [
                    rounded_coordinate(sum(longitudes) / count, "data-center-hub"),
                    rounded_coordinate(sum(latitudes) / count, "data-center-hub"),
                ],
            },
            "properties": {
                "key": hub_id,
                "layer": "data-center-hub",
                "count": count,
                "capacity_mw": capacity,
                "states": ", ".join(states[:4]),
                "status_counts": status_counts,
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def write_detail_files(
    output_dir: Path,
    stem: str,
    layer: str,
    records: list[dict[str, Any]],
    fields: list[str],
    generated_at: str,
) -> list[dict[str, Any]]:
    shards: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record_key(record, layer)
        detail_file = detail_file_for_key(stem, key)
        filename = detail_file.removeprefix("data/")
        shard = shards.setdefault(filename, {})
        shard[key] = records_for_json([record], fields)[0]

    output_files: list[dict[str, Any]] = []
    for filename, details in sorted(shards.items()):
        path = output_dir / filename
        write_json(path, {
            "generatedAt": generated_at,
            "layer": layer,
            "details": details,
        })
        output_files.append(file_record(stem, path))
    return output_files


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path = resolve_output_file(path, {".json", ".geojson"}, "Map data file")
    path.write_text(
        json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")),
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(label: str, path: Path) -> dict[str, Any]:
    path = project_path(path)
    return {
        "label": label,
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def source_metadata() -> list[dict[str, str]]:
    return [
        {
            "name": "Compute Atlas",
            "url": COMPUTE_ATLAS_URL,
            "description": "Open, source-cited U.S. data-center facility dataset (CC BY 4.0).",
        },
        {
            "name": "EIA-860M",
            "url": EIA_860M_PAGE_ALT,
            "description": "Official monthly U.S. generator inventory for operating and planned power plants.",
        },
        {
            "name": "Water source reference points",
            "url": USGS_NHD_URL,
            "description": "Named surface-water context points for proximity calculations.",
        },
    ]


def summary_payload(
    data_centers: list[dict[str, Any]],
    nuclear_plants: list[dict[str, Any]],
    power_plants: list[dict[str, Any]],
    water_sources: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "generatedAt": generated_at,
        "sources": source_metadata(),
        "recordCounts": {
            "dataCenters": len(data_centers),
            "nuclearPlants": len(nuclear_plants),
            "powerPlants": len(power_plants),
            "waterSources": len(water_sources),
        },
        "capacityMw": {
            "dataCenters": round(total_capacity(data_centers), 1),
            "nuclearPlants": round(total_capacity(nuclear_plants), 1),
            "powerPlants": round(total_capacity(power_plants), 1),
        },
        "dataFiles": {
            "dataCenterHubs": "data-center-hubs.geojson",
            "dataCenters": "data-centers.geojson",
            "nuclearPlants": "nuclear-plants.geojson",
            "powerPlants": "power-plants.geojson",
            "waterSources": "water-sources.geojson",
            "details": {
                "dataCenters": "details/data-centers-00.json",
                "nuclearPlants": "details/nuclear-plants.json",
                "powerPlants": "details/power-plants-00.json",
                "waterSources": "details/water-sources.json",
            },
        },
    }


def write_map_data_files(
    output_dir: Path,
    data_centers: list[dict[str, Any]],
    nuclear_plants: list[dict[str, Any]],
    power_plants: list[dict[str, Any]],
    water_sources: list[dict[str, Any]],
    generated_at: str,
    input_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output_dir = resolve_output_dir(output_dir)
    details_dir = output_dir / "details"
    if details_dir.exists():
        for stale_file in details_dir.glob("*.json"):
            stale_file.unlink()

    files = [
        ("dataCenterHubs", "data-center-hubs.geojson", data_center_hub_collection(data_centers)),
        ("dataCenters", "data-centers.geojson", feature_collection(data_centers, "data-center", "data-centers")),
        ("nuclearPlants", "nuclear-plants.geojson", feature_collection(nuclear_plants, "nuclear", "nuclear-plants")),
        ("powerPlants", "power-plants.geojson", feature_collection(power_plants, "power", "power-plants")),
        ("waterSources", "water-sources.geojson", feature_collection(water_sources, "water", "water-sources")),
    ]

    output_files: list[dict[str, Any]] = []
    for label, filename, payload in files:
        path = output_dir / filename
        write_json(path, payload)
        output_files.append(file_record(label, path))

    output_files.extend(write_detail_files(
        output_dir,
        "data-centers",
        "data-center",
        data_centers,
        DATA_CENTER_FIELDS,
        generated_at,
    ))
    output_files.extend(write_detail_files(
        output_dir,
        "nuclear-plants",
        "nuclear",
        nuclear_plants,
        NUCLEAR_FIELDS,
        generated_at,
    ))
    output_files.extend(write_detail_files(
        output_dir,
        "power-plants",
        "power",
        power_plants,
        POWER_FIELDS,
        generated_at,
    ))
    output_files.extend(write_detail_files(
        output_dir,
        "water-sources",
        "water",
        water_sources,
        WATER_FIELDS,
        generated_at,
    ))

    summary = summary_payload(data_centers, nuclear_plants, power_plants, water_sources, generated_at)
    summary["changeRecord"] = {
        "generator": "script.py",
        "format": "split-json-geojson",
        "inputs": input_files,
        "outputs": output_files,
    }
    write_json(output_dir / "map-summary.json", summary)
    output_files.append(file_record("summary", output_dir / "map-summary.json"))
    return output_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build data-center map data files.")
    parser.add_argument("--compute-atlas-json", type=Path, default=Path("compute-atlas-data-centers.json"))
    parser.add_argument("--compute-atlas-all-json", type=Path, default=Path("compute-atlas-facilities.json"))
    parser.add_argument("--refresh-compute-atlas", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=Path("cache"))
    parser.add_argument("--refresh-eia", action="store_true")
    parser.add_argument("--water-sources", type=Path, default=Path("water_sources.csv"))
    parser.add_argument("--output-prefix", default="us_ai_datacenters_nuclear_map")
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-db", type=Path, default=Path("private/map-data.sqlite"))
    parser.add_argument("--skip-sqlite", action="store_true")
    parser.add_argument("--output-js", type=Path, default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output_js:
        warnings.warn(
            "--output-js is deprecated and ignored; split JSON/GeoJSON files are written to --output-dir.",
            RuntimeWarning,
            stacklevel=2,
        )
    generated_at = dt.datetime.now().strftime("%B %d, %Y %I:%M %p")

    eia_label, eia_url, workbook_path, from_cache = download_eia_workbook(
        args.cache_dir,
        refresh=args.refresh_eia,
    )
    if from_cache:
        warnings.warn(f"Using cached EIA-860M workbook: {workbook_path.name}", RuntimeWarning, stacklevel=2)

    data_center_facilities = load_compute_atlas_records(
        args.compute_atlas_json,
        refresh=args.refresh_compute_atlas,
    )
    compute_power_facilities = load_compute_atlas_power_records(
        args.compute_atlas_all_json,
        refresh=args.refresh_compute_atlas,
    )

    data_centers = normalize_data_centers(data_center_facilities)
    eia_power_plants = load_eia_power_plants(workbook_path, eia_label, eia_url)
    compute_power_plants = normalize_compute_power_records(compute_power_facilities)
    power_plants = sorted(
        eia_power_plants + compute_power_plants,
        key=lambda item: (str(item.get("state", "")), str(item.get("name", ""))),
    )
    nuclear_plants = [
        plant for plant in power_plants
        if bool(plant.get("is_nuclear")) or "nuclear" in str(plant.get("technologies", "")).lower()
    ]
    water_sources = load_water_sources(args.water_sources)

    enrich_data_centers(data_centers, nuclear_plants, power_plants, water_sources)

    input_files = [
        file_record("Compute Atlas data centers", resolve_output_file(args.compute_atlas_json, {".json"}, "Compute Atlas data-center JSON")),
        file_record("Compute Atlas facilities", resolve_output_file(args.compute_atlas_all_json, {".json"}, "Compute Atlas facilities JSON")),
        file_record("EIA-860M workbook", workbook_path),
        file_record("Water sources", validate_existing_file(args.water_sources, {".csv"}, MAX_CSV_FILE_BYTES, "Water source CSV")),
    ]

    prefix = project_path(Path(args.output_prefix))
    write_csv(prefix.with_name(f"{prefix.name}_data_centers.csv"), data_centers, DATA_CENTER_FIELDS)
    write_csv(prefix.with_name(f"{prefix.name}_ai_datacenters.csv"), data_centers, DATA_CENTER_FIELDS)
    write_csv(prefix.with_name(f"{prefix.name}_power_plants.csv"), power_plants, POWER_FIELDS)
    write_csv(prefix.with_name(f"{prefix.name}_nuclear_plants.csv"), nuclear_plants, NUCLEAR_FIELDS)
    write_csv(prefix.with_name(f"{prefix.name}_water_sources.csv"), water_sources, WATER_FIELDS)
    output_files = write_map_data_files(
        args.output_dir,
        data_centers,
        nuclear_plants,
        power_plants,
        water_sources,
        generated_at,
        input_files,
    )
    sqlite_path: Path | None = None
    if not args.skip_sqlite:
        sqlite_path = write_sqlite_database(
            args.output_db,
            data_centers,
            nuclear_plants,
            power_plants,
            water_sources,
            generated_at,
            input_files,
            output_files,
        )

    print(f"Data centers: {len(data_centers)} from Compute Atlas")
    print(f"Power plants: {len(power_plants)} from EIA-860M + Compute Atlas power-generation records")
    print(f"Nuclear sites: {len(nuclear_plants)} operating/planned/restart/SMR records")
    print(f"Water sources: {len(water_sources)} named reference points")
    print(f"Data-center capacity shown: {total_capacity(data_centers) / 1000:,.1f} GW")
    print(f"Map data written to: {project_path(args.output_dir)}")
    if sqlite_path:
        print(f"SQLite test database written to: {sqlite_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
