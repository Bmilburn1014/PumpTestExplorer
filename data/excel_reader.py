# data/excel_reader.py
#
# FAST reader for detail .xlsm test files.
#
# Opens .xlsm as zip, streams sheet XML with iterparse, stops
# decompressing as soon as we pass row 60. Over network shares
# this means we only transfer ~5-10% of the sheet XML.
#
# Also provides batch_copy_to_local() to pre-copy files from
# a network share to a local temp folder for faster access.

import pandas as pd
import re
import os
import shutil
import zipfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.etree.ElementTree import iterparse

# ── Column mapping ───────────────────────────────────────────────
TARGET_COLS = {
    "DN": "curve_no",       "DO": "flow",
    "DP": "speed",          "DQ": "power_in",
    "DR": "power_bowl_motor", "DS": "power_bowl_dyno",
    "DT": "power_pump_motor", "DU": "power_pump_dyno",
    "DV": "tdh_bowl",       "DW": "tdh_pump",
    "DX": "eff_bowl_motor", "DY": "eff_bowl_dyno",
    "DZ": "eff_pump_motor", "EA": "eff_pump_dyno",
    "EB": "eff_overall",    "EC": "npsha",
}
TARGET_COL_SET = set(TARGET_COLS.keys())

HEADER_ROW = 10
DATA_START_ROW = 11
MAX_DATA_ROW = 60

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

FLOW_COLS = {"flow"}
HEAD_COLS = {"tdh_bowl", "tdh_pump", "npsha"}
POWER_COLS = {"power_in", "power_bowl_motor", "power_bowl_dyno",
              "power_pump_motor", "power_pump_dyno"}

FLOW_CONVERSIONS = {
    "gpm": 1.0, "m3/hr": 4.40287, "m^3/hr": 4.40287,
    "m3/h": 4.40287, "m^3/h": 4.40287,
    "m3/sec": 15850.3, "m^3/sec": 15850.3, "m3/s": 15850.3, "m^3/s": 15850.3,
    "l/min": 0.264172, "lpm": 0.264172,
    "l/sec": 15.8503, "l/s": 15.8503, "lps": 15.8503,
}
HEAD_CONVERSIONS = {
    "ft": 1.0, "feet": 1.0, "psi": 2.31,
    "m": 3.28084, "meter": 3.28084, "meters": 3.28084,
    "kpa": 0.334553, "bar": 33.4553,
}
POWER_CONVERSIONS = {"hp": 1.0, "kw": 1.34102}

_COL_RE = re.compile(r'^([A-Z]+)(\d+)$')

# ── Valid detail file extensions (in order of preference) ────────
_DETAIL_EXTENSIONS = (".xlsm", ".xlsx", ".xls")


def _detect_unit(header_text: str, table: dict) -> float:
    if not header_text:
        return 1.0
    text = header_text.strip().lower()
    paren = re.search(r'\(([^)]+)\)', text)
    unit_text = paren.group(1).strip() if paren else text
    if unit_text in table:
        return table[unit_text]
    for key, factor in table.items():
        if key in unit_text:
            return factor
    for key, factor in table.items():
        if key in text:
            return factor
    return 1.0

def excel_col_to_index(col):
    idx = 0
    for c in col:
        idx = idx * 26 + (ord(c.upper()) - ord('A') + 1)
    return idx - 1

def _find_sheet_path(zf, sheet_name):
    """Find the XML path for a sheet by name."""
    try:
        wb_ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        r_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        rid = None
        for el in wb_root.iter(f"{wb_ns}sheet"):
            if el.get("name", "").strip() == sheet_name:
                rid = el.get(f"{r_ns}id")
                break
        if not rid:
            return None
        rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rels_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
        for rel in rels_root.iter(f"{rels_ns}Relationship"):
            if rel.get("Id") == rid:
                t = rel.get("Target", "")
                return f"xl/{t}" if not t.startswith("/") else t.lstrip("/")
    except Exception:
        pass
    for i in range(1, 10):
        p = f"xl/worksheets/sheet{i}.xml"
        if p in zf.namelist():
            return p
    return None


def _load_shared_strings(zf):
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        strings = []
        for si in root.iter(f"{NS}si"):
            parts = [t.text for t in si.iter(f"{NS}t") if t.text]
            strings.append("".join(parts))
        return strings
    except Exception:
        return []


def _cell_value(cell_elem, shared_strings):
    """Extract value from a <c> element."""
    ct = cell_elem.get("t", "")
    if ct == "inlineStr":
        for t in cell_elem.iter(f"{NS}t"):
            if t.text:
                return t.text
        for t in cell_elem.iter("t"):
            if t.text:
                return t.text
        return None
    v = cell_elem.find(f"{NS}v")
    if v is None:
        v = cell_elem.find("v")
    if v is None or v.text is None:
        return None
    if ct == "s":
        idx = int(v.text)
        return shared_strings[idx] if idx < len(shared_strings) else None
    try:
        return float(v.text)
    except ValueError:
        return v.text


# ═════════════════════════════════════════════════════════════════
# FUZZY FILE FINDER — robust detail file lookup
# ═════════════════════════════════════════════════════════════════

# Cache of {directory_path: {normalized_stem: actual_filename}}
_dir_cache = {}


def _build_dir_cache(directory: str) -> dict:
    """
    Scan a directory and build a lookup of normalized filename stems
    to actual filenames. Cached per directory.

    Normalization: strip whitespace, lowercase, remove extension.
    """
    if directory in _dir_cache:
        return _dir_cache[directory]

    cache = {}
    try:
        for fname in os.listdir(directory):
            # Only index files with valid detail extensions
            stem, ext = os.path.splitext(fname)
            if ext.lower() not in _DETAIL_EXTENSIONS:
                continue
            normalized = stem.strip().lower()
            # First file wins (prefer .xlsm over .xlsx if both exist,
            # but listdir order isn't guaranteed — sort by extension
            # preference if there's a collision)
            if normalized not in cache:
                cache[normalized] = fname
            else:
                # Keep the one with the higher-priority extension
                existing_ext = os.path.splitext(cache[normalized])[1].lower()
                new_ext = ext.lower()
                existing_rank = _DETAIL_EXTENSIONS.index(existing_ext) if existing_ext in _DETAIL_EXTENSIONS else 99
                new_rank = _DETAIL_EXTENSIONS.index(new_ext) if new_ext in _DETAIL_EXTENSIONS else 99
                if new_rank < existing_rank:
                    cache[normalized] = fname
    except FileNotFoundError:
        pass

    _dir_cache[directory] = cache
    return cache


def clear_dir_cache(directory: str = None):
    """
    Clear the directory file cache. Call if files have been added/removed.

    Args:
        directory: Specific directory to clear. If None, clears all.
    """
    if directory is None:
        _dir_cache.clear()
    else:
        _dir_cache.pop(directory, None)


def find_detail_file(directory: str, test_id: str) -> str | None:
    """
    Find a detail file in a directory matching the given test_id.

    Handles common mismatches:
      - Whitespace in test_id or filename
      - Case differences (upper/lower)
      - Extension differences (.xlsm, .xlsx, .xls)
      - Leading/trailing spaces in the index data

    Args:
        directory: Folder to search (e.g. "data/14HXB")
        test_id:   Value from the index's test_id column

    Returns:
        Full path to the matched file, or None if not found.
    """
    if not test_id or not directory:
        return None

    # Normalize the test_id the same way we normalize filenames
    normalized_id = str(test_id).strip().lower()

    # Check the directory cache
    cache = _build_dir_cache(directory)

    # 1. Exact normalized match
    if normalized_id in cache:
        return os.path.join(directory, cache[normalized_id])

    # 2. Try without all whitespace (in case of embedded spaces)
    no_space_id = normalized_id.replace(" ", "")
    for norm_stem, fname in cache.items():
        if norm_stem.replace(" ", "") == no_space_id:
            return os.path.join(directory, fname)

    # 3. Contains match — test_id is a substring of filename or vice versa
    #    (handles cases where filename has extra suffixes/prefixes)
    for norm_stem, fname in cache.items():
        if normalized_id in norm_stem or norm_stem in normalized_id:
            return os.path.join(directory, fname)

    return None


def find_detail_files_batch(directory: str, test_ids: list) -> dict:
    """
    Find detail files for a list of test_ids at once.

    Builds the directory cache once, then looks up each test_id.

    Args:
        directory: Folder to search
        test_ids:  List of test_id values from the index

    Returns:
        {test_id: full_path_or_None} for each input test_id.
    """
    # Pre-build the cache once for the directory
    _build_dir_cache(directory)

    return {
        tid: find_detail_file(directory, tid)
        for tid in test_ids
    }


# ═════════════════════════════════════════════════════════════════
# BATCH LOCAL COPY — pre-copy files from network to local temp
# ═════════════════════════════════════════════════════════════════

_local_cache_dir = None


def get_local_cache_dir():
    """Get or create a temp dir for local copies of detail files."""
    global _local_cache_dir
    if _local_cache_dir is None or not os.path.exists(_local_cache_dir):
        _local_cache_dir = tempfile.mkdtemp(prefix="px_detail_")
    return _local_cache_dir


def copy_to_local(network_path: str) -> str:
    """
    Copy a file from network share to local temp dir.
    Returns the local path. Skips if already copied.
    """
    cache_dir = get_local_cache_dir()
    # Use the filename as-is (test IDs are unique)
    fname = os.path.basename(network_path)
    local_path = os.path.join(cache_dir, fname)

    if os.path.exists(local_path):
        return local_path

    try:
        shutil.copy2(network_path, local_path)
        return local_path
    except Exception:
        return network_path  # Fall back to network path


def batch_copy_to_local(paths: list) -> dict:
    """
    Copy multiple files from network to local temp in one batch.
    Returns {str(original_path): local_path_str}.
    Uses ThreadPoolExecutor for parallel copies.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    result = {}
    to_copy = []
    cache_dir = get_local_cache_dir()

    for p in paths:
        p_str = str(p)
        fname = os.path.basename(p_str)
        local = os.path.join(cache_dir, fname)
        if os.path.exists(local):
            result[p_str] = local
        else:
            to_copy.append(p_str)

    if not to_copy:
        return result

    done = 0

    def _copy_one(src):
        dst = os.path.join(cache_dir, os.path.basename(src))
        try:
            shutil.copy2(src, dst)
            return src, dst
        except Exception as e:
            return src, src

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_copy_one, p): p for p in to_copy}
        for future in as_completed(futures):
            src, dst = future.result()
            result[src] = dst
            done += 1

    # Also include the already-cached ones
    for p in paths:
        if p not in result:
            result[p] = p

    print(f"Local copy done: {len(to_copy)} files")
    return result


# ═════════════════════════════════════════════════════════════════
# MAIN READER
# ═════════════════════════════════════════════════════════════════

def read_detail_file(filepath: str) -> pd.DataFrame:
    """
    Read detail file data from either:
      - legacy .xls files (via calamine)
      - .xlsx/.xlsm files (via XML streaming parser)

    Both paths populate:
      header_data
      data_rows

    The downstream conversion/unit logic remains identical.
    """
    from config import DETAIL_SHEET_NAME
    print(f"ENTERED read_detail_file: {filepath}", flush=True)
    ext = Path(filepath).suffix.lower()
    print(f"File extension: {ext}", flush=True)
    header_data = {}
    data_rows = []
    empty_streak = 0

    # ============================================================
    # XLS PATH (Calamine)
    # ============================================================
    if ext == ".xls":
        
        try:
            print("about to read .xls file with calamine", flush=True)
            raw = pd.read_excel(
            filepath,
            sheet_name=DETAIL_SHEET_NAME,
            engine="calamine",
            header=None,
        )
            print("read .xls correctly with calamine", flush=True)
        except Exception:
            import traceback
            print("✗ Cannot read .xls", flush=True)
            traceback.print_exc()
            return pd.DataFrame()

        try:
            print(raw.shape, flush=True)

        except Exception:
            print("cannot read shape of raw", flush=True)
        
        for excel_row in range(HEADER_ROW, MAX_DATA_ROW + 1):

            row_idx = excel_row - 1

            if row_idx >= len(raw):
                break

            row_data = {}
            if row_data:
                print(f"Excel row {excel_row}: {row_data}")

            for col_letter, field_name in TARGET_COLS.items():

                col_idx = excel_col_to_index(col_letter)

                if col_idx >= raw.shape[1]:
                    continue

                val = raw.iat[row_idx, col_idx]
                
                if pd.notna(val):
                    row_data[field_name] = val

            if excel_row == HEADER_ROW:
                header_data = row_data

            elif excel_row >= DATA_START_ROW:
                if row_data:
                    data_rows.append(row_data)
                    empty_streak = 0
                else:
                    empty_streak += 1

                    if empty_streak >= 3:
                        break

    # ============================================================
    # XLSX / XLSM PATH (existing XML parser)
    # ============================================================
    elif ext in [".xlsx", ".xlsm"]:
        print("in xlsm/xlsx case, using zipfile and iterparse", flush=True)
        try:
            zf = zipfile.ZipFile(filepath, "r")
        except Exception as e:
            print(f"✗ Cannot open: {e}")
            return pd.DataFrame()

        sheet_path = _find_sheet_path(zf, DETAIL_SHEET_NAME)
        if not sheet_path:
            print(f"✗ No '{DETAIL_SHEET_NAME}' sheet")
            zf.close()
            return pd.DataFrame()

        shared_strings = _load_shared_strings(zf)

        try:
            stream = zf.open(sheet_path)
            context = iterparse(stream, events=("end",))

            for event, elem in context:
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

                if tag != "row":
                    continue

                row_num = int(elem.get("r", 0))

                if row_num < HEADER_ROW:
                    elem.clear()
                    continue

                if row_num > MAX_DATA_ROW:
                    elem.clear()
                    break

                row_data = {}

                for child in elem:
                    child_tag = (
                        child.tag.split("}")[-1]
                        if "}" in child.tag
                        else child.tag
                    )

                    if child_tag != "c":
                        continue

                    ref = child.get("r", "")
                    m = _COL_RE.match(ref)

                    if not m:
                        continue

                    col_letter = m.group(1)

                    if col_letter not in TARGET_COL_SET:
                        continue

                    val = _cell_value(child, shared_strings)

                    if val is not None:
                        row_data[TARGET_COLS[col_letter]] = val

                if row_num == HEADER_ROW:
                    header_data = row_data

                elif row_num >= DATA_START_ROW:

                    if row_data:
                        data_rows.append(row_data)
                        empty_streak = 0
                    else:
                        empty_streak += 1

                        if empty_streak >= 3:
                            elem.clear()
                            break

                elem.clear()

            stream.close()

        except Exception as e:
            print(f"✗ Parse error: {e}")
            zf.close()
            return pd.DataFrame()

        zf.close()

    else:
        print(f"✗ Unsupported file type: {ext}")
        return pd.DataFrame()

    # ============================================================
    # SHARED PROCESSING
    # ============================================================

    if not data_rows:
        return pd.DataFrame()

    flow_f = _detect_unit(
        str(header_data.get("flow", "")),
        FLOW_CONVERSIONS,
    )

    head_f = _detect_unit(
        str(header_data.get("tdh_bowl", "")),
        HEAD_CONVERSIONS,
    )

    power_f = _detect_unit(
        str(header_data.get("power_bowl_motor", "")),
        POWER_CONVERSIONS,
    )

    needs = (
        flow_f != 1.0
        or head_f != 1.0
        or power_f != 1.0
    )

    if needs:
        print(
            f"  Units: flow ×{flow_f:.3g}, "
            f"head ×{head_f:.3g}, "
            f"power ×{power_f:.3g}"
        )

    df = pd.DataFrame(data_rows)

    for col in TARGET_COLS.values():
        if col in df.columns and col != "curve_no":
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "flow" in df.columns:
        df = df.dropna(subset=["flow"])

    if needs:

        if flow_f != 1.0:
            for c in FLOW_COLS:
                if c in df.columns:
                    df[c] *= flow_f

        if head_f != 1.0:
            for c in HEAD_COLS:
                if c in df.columns:
                    df[c] *= head_f

        if power_f != 1.0:
            for c in POWER_COLS:
                if c in df.columns:
                    df[c] *= power_f

    return df