# data/path_resolver.py
#
# Builds file paths to detail .xlsm files from test IDs.
#
# PPU (inline) detail files:
#   Located in year subfolders on a network drive:
#     \\server\base\2024\{test_id}.xlsm
#     \\server\base\2025\{test_id}.xlsm
#
#   Filenames are a "loose fit" to the test ID — the file might
#   end with P or D (or other suffixes) while the index has a
#   different ending.  We match on the core prefix.
#
# Vertical pump detail files:
#   Stored in subdirectories named by bowl size + model name,
#   with deeper nesting depending on pump type:
#
#   Standard:
#     \\server\vt_base\{bowl_model}\{impeller_part_number}\{test_id}.xlsm
#
#   Fire pump:
#     \\server\vt_base\{bowl_model}\...\fire pumps\...\{test_id}.xlsm
#     (folder names include model codes, flow rates, stage counts, etc.)
#
#   Because folder names contain unpredictable model prefixes, units,
#   and formatting, the vertical resolver searches the *entire* bowl
#   model directory tree rather than trying to construct exact paths.
#
# The resolver tries, in order:
#   1. Exact match in the expected directory
#   2. Fuzzy match (case-insensitive, whitespace-tolerant)
#   3. Prefix match (last character varies: P↔D etc.)
#   4. Search all year folders if year is unknown (inline only)
#   5. Recursive tree search as final fallback (vertical pumps)

import os
import re
import math
from pathlib import Path
from functools import lru_cache
from config import DETAIL_DIRS, YEAR_FOLDER_INDEXES, DETAIL_FILE_DIR

EXTENSIONS = (".xlsm", ".xlsx", ".xls")

VERTICAL_CLEANUP_DIR_NAME = "_Cleanup Error"


def build_detail_path(test_id: str, row=None,
                      index_name: str = None,
                      detail_subdir: str = None) -> Path | None:
    """
    Resolve the full path to a detail file for a given test_id.

    Args:
        test_id:       Test identifier from the index (e.g., '99272055410010A2P')
        row:           Optional pandas Series with the full index row
                       (used to extract test_date → year, impeller_part_number,
                       rated_flow, number_of_stages, is_fire_pump, etc.)
        index_name:    Which index file this came from ('ppu_test_log', etc.)
        detail_subdir: Optional subdirectory under the base path
                       (e.g. '14HXB' for vertical pumps)

    Returns:
        Path to the detail file, or None if not found.
    """
    if not test_id or not str(test_id).strip():
        return None

    test_id = str(test_id).strip()

    # Pick the correct base directory
    base = Path(DETAIL_DIRS.get(index_name, DETAIL_FILE_DIR))
    uses_year_folders = index_name in YEAR_FOLDER_INDEXES

    if uses_year_folders:
        return _resolve_year_folder(test_id, base, row)

    elif detail_subdir:
        # Vertical pump: search possible bowl-model folder variants.
        # Example: detail_subdir="12MB" should check both:
        #   vt_base/12MB
        #   vt_base/M12MB
        return _resolve_vertical_model_variants(
            test_id=test_id,
            vt_base=base,
            detail_subdir=detail_subdir,
            row=row,
        )

    else:
        return _resolve_flat(test_id, base)




# ═════════════════════════════════════════════════════════════
# YEAR-FOLDER RESOLVER  (PPU / inline pumps)
# ═════════════════════════════════════════════════════════════

def _resolve_year_folder(test_id: str, base: Path, row=None) -> Path | None:
    """
    Find a detail file in the year-folder structure.

    Search order:
      1. Exact match in expected year folder
      2. Loose match (prefix) in expected year folder
      3. Adjacent years (± 1)
      4. If no year known, search all year folders
    """
    year = _extract_year(row)

    if year:
        # Search the specific year folder first
        result = _search_year_folder(test_id, base / str(year))
        if result:
            return result

        # Try adjacent years (± 1) in case date is near year boundary
        for delta in (1, -1):
            result = _search_year_folder(test_id, base / str(year + delta))
            if result:
                return result

    # No year or not found — scan all year folders
    return _search_all_year_folders(test_id, base)


def _search_year_folder(test_id: str, year_dir: Path) -> Path | None:
    """
    Search a single year folder for the detail file.

    1. Exact filename match
    2. Fuzzy match (case-insensitive, whitespace-tolerant)
    3. Loose match: strip trailing P/D suffix and match prefix
    """
    if not year_dir.is_dir():
        return None

    # 1. Exact match
    for ext in EXTENSIONS:
        candidate = year_dir / f"{test_id}{ext}"
        if candidate.exists():
            return candidate

    # 2. Fuzzy match
    fuzzy = _fuzzy_find(test_id, year_dir)
    if fuzzy:
        return fuzzy

    # 3. Loose match — the core prefix minus the last character
    prefix = _get_search_prefix(test_id)
    if prefix and prefix != test_id:
        matches = _glob_prefix(year_dir, prefix)
        if matches:
            return _best_match(test_id, matches)

    return None


def _search_all_year_folders(test_id: str, base: Path) -> Path | None:
    """Scan all year folders (most recent first) for the file."""
    if not base.is_dir():
        return None

    year_dirs = _get_year_folders(base)

    for year_dir in year_dirs:
        result = _search_year_folder(test_id, year_dir)
        if result:
            return result

    return None


# ═════════════════════════════════════════════════════════════
# VERTICAL PUMP RESOLVER
# ═════════════════════════════════════════════════════════════
def _resolve_vertical_model_variants(
    test_id: str,
    vt_base: Path,
    detail_subdir: str,
    row=None,
) -> Path | None:
    """
    Resolve vertical pump detail files when the bowl-model folder may have
    a one-letter prefix.

    Examples:
      detail_subdir='12MB'  -> searches vt_base/12MB, vt_base/M12MB
      detail_subdir='M12MB' -> searches vt_base/M12MB, vt_base/12MB

    The first successful detail-file match wins.
    """
    if not vt_base or not detail_subdir:
        return None

    candidate_dirs = _vertical_bowl_model_dirs(vt_base, detail_subdir)

    for folder in candidate_dirs:
        result = _resolve_vertical(test_id, folder, row)
        if result:
            return result

    return None


def _resolve_vertical(test_id: str, base: Path, row=None) -> Path | None:
    """
    Find a detail file anywhere under the bowl-model directory.

    Walks the entire tree (e.g. \\server\\vt_base\\14HXB\\**) applying
    exact, fuzzy, and prefix matching — just like searching in
    File Explorer.

    If row metadata is available, checks the expected subfolder first
    for speed, then falls back to the full recursive search.

    Args:
        test_id: Test identifier to find
        base:    Bowl-model directory (e.g. ...\\14HXB)
        row:     Optional index row with metadata fields
    """
    # Try the targeted path first if we have row metadata
    if row is not None:
        target_dir = _build_vertical_subdir(base, row)
        if target_dir and target_dir.is_dir():
            result = _resolve_flat(test_id, target_dir)
            if result:
                return result

    # Search the entire tree under the bowl-model directory
    return _resolve_flat_recursive(test_id, base)

def _vertical_bowl_model_dirs(vt_base: Path, detail_subdir: str) -> list[Path]:
    """
    Build candidate bowl-model directories for vertical pumps.

    Handles folder naming variants such as:
      12MB  <->  M12MB
      12AES <->  A12AES
<->  H14HXB

    Also always checks the sibling cleanup folder:
      vt_base/_Cleanup Error

    Search order:
      1. Provided detail_subdir
      2. Prefix/non-prefix model variant
      3. Existing case/space-insensitive folder matches
      4. Existing one-letter-prefixed folder ending in requested model
      5. _Cleanup Error
    """
    raw = str(detail_subdir).strip()
    if not raw:
        return []

    variants = []

    def add_variant(name: str):
        name = str(name).strip()
        if name and name not in variants:
            variants.append(name)

    # Always search the provided folder name first.
    add_variant(raw)

    # Case 1:
    #   12MB -> M12MB
    #   12AES -> A12AES
    m = re.match(r'^(\d{1,2})([A-Za-z].*)$', raw)
    if m:
        bowl_size = m.group(1)
        model_letters = m.group(2)
        prefix = model_letters[0]
        add_variant(f"{prefix}{bowl_size}{model_letters}")

    # Case 2:
    #   M12MB -> 12MB
    #   A12AES -> 12AES
    m = re.match(r'^([A-Za-z])(\d{1,2})([A-Za-z].*)$', raw)
    if m:
        prefix = m.group(1)
        bowl_size = m.group(2)
        model_letters = m.group(3)

        # Only strip the prefix if it looks like a duplicated model prefix.
        # Example: M12MB -> prefix M and model MB.
        if model_letters.upper().startswith(prefix.upper()):
            add_variant(f"{bowl_size}{model_letters}")

    candidates = []

    def add_candidate(path: Path):
        if path not in candidates:
            candidates.append(path)

    # Exact candidate paths first, in preferred order.
    for variant in variants:
        add_candidate(vt_base / variant)

    # Inspect actual folders under vt_base for case/space variants.
    try:
        children = [
            Path(entry.path)
            for entry in os.scandir(str(vt_base))
            if entry.is_dir()
        ]
    except OSError:
        children = []

    normalized_variants = {
        v.strip().lower().replace(" ", "")
        for v in variants
    }

    for child in children:
        child_norm = child.name.strip().lower().replace(" ", "")

        if child_norm in normalized_variants:
            add_candidate(child)

    # If requested 12MB, allow any one-letter-prefixed folder ending in 12MB.
    # Example: M12MB.
    raw_norm = raw.lower().replace(" ", "")

    for child in children:
        child_norm = child.name.strip().lower().replace(" ", "")

        if (
            len(child_norm) == len(raw_norm) + 1
            and child_norm.endswith(raw_norm)
            and child_norm[0].isalpha()
        ):
            add_candidate(child)

    # Always check the shared cleanup folder.
    cleanup_norm = VERTICAL_CLEANUP_DIR_NAME.lower().replace(" ", "")

    # Prefer the actual existing folder if found, to preserve exact casing.
    cleanup_added = False
    for child in children:
        child_norm = child.name.strip().lower().replace(" ", "")

        if child_norm == cleanup_norm:
            add_candidate(child)
            cleanup_added = True
            break

    # Add the expected path even if it does not currently exist.
    # _resolve_vertical() will safely return None if it is not a directory.
    if not cleanup_added:
        add_candidate(vt_base / VERTICAL_CLEANUP_DIR_NAME)

    return candidates


def _build_vertical_subdir(base: Path, row) -> Path | None:
    """
    Construct the expected subdirectory path from index row metadata.

    This is a best-effort optimistic lookup. Folder names on disk
    often contain extra info (model codes, units, rpm) that we can't
    predict, so this may not match. The recursive fallback handles
    those cases.

    Standard:    {base}/{impeller_part_number}
    Fire pump:   {base}/fire pumps/{rated_flow}/{number_of_stages}

    Returns:
        Path to the target directory, or None if required fields are missing.
    """
    is_fire = _row_value(row, "is_fire_pump")
    if is_fire:
        rated_flow = _row_value(row, "rated_flow")
        num_stages = (_row_value(row, "number_of_stages")
                      or _row_value(row, "num_stages"))
        if rated_flow is not None and num_stages is not None:
            return base / "fire pumps" / str(rated_flow) / str(num_stages)
    else:
        impeller_pn = (_row_value(row, "impeller_part_number")
                       or _row_value(row, "imp_part_num")
                       or _row_value(row, "impeller_part"))
        if impeller_pn:
            return base / str(impeller_pn).strip()

    return None


def _resolve_flat_recursive(test_id: str, base: Path,
                            max_depth: int = 5) -> Path | None:
    """
    Find a detail file anywhere under the base directory tree.

    Uses a cached tree index: the first call for a given base directory
    scans the entire tree once and builds an in-memory lookup of
    {normalized_stem: full_path}.  All subsequent lookups for the same
    base directory are instant O(1) dict hits.

    This turns N tests × full network tree walk into 1 tree walk + N
    dict lookups — orders of magnitude faster on network drives.

    Args:
        test_id:   Test identifier to find
        base:      Root directory to start searching
        max_depth: Limit recursion depth (for the one-time scan)
    """
    if not base.is_dir():
        return None

    index = _get_tree_index(base, max_depth)
    if not index:
        return None

    tid_lower = test_id.lower().strip()
    tid_no_space = tid_lower.replace(" ", "")

    # Tier 1: Exact normalized match
    if tid_lower in index:
        return index[tid_lower]

    # Tier 2: Whitespace-collapsed match
    for stem, path in index.items():
        if stem.replace(" ", "") == tid_no_space:
            return path

    # Tier 3: Prefix match (strip trailing variant letter)
    search_prefix = _get_search_prefix(test_id)
    if search_prefix and search_prefix.lower() != tid_lower:
        prefix_lower = search_prefix.lower()
        candidates = [p for s, p in index.items()
                      if s.startswith(prefix_lower)]
        if candidates:
            return _best_match(test_id, candidates)

    return None


# ── Tree index cache ────────────────────────────────────────
# {str(base_directory): {normalized_stem: Path}}
_tree_index_cache = {}


def prewarm_tree_index(base: Path, max_depth: int = 5) -> int:
    """
    Pre-build the tree file index with parallel subdirectory scanning.

    For network directories, this is dramatically faster than sequential
    scanning because each thread handles a separate subdirectory tree,
    and network I/O (not CPU) is the bottleneck.

    Call this ONCE before resolving multiple test IDs against the same
    base directory.

    Args:
        base:      Root directory to index
        max_depth: Maximum recursion depth

    Returns:
        Number of detail files found in the index.
    """
    key = str(base)
    if key in _tree_index_cache:
        return len(_tree_index_cache[key])

    index = {}
    _scan_tree_parallel(base, max_depth, index)
    _tree_index_cache[key] = index
    return len(index)


def _get_tree_index(base: Path, max_depth: int = 5) -> dict:
    """
    Get or build the tree file index for a base directory.

    If not already cached, scans with parallel subdirectory scanning.
    The result is cached so subsequent calls return instantly.
    """
    key = str(base)
    if key in _tree_index_cache:
        return _tree_index_cache[key]

    index = {}
    _scan_tree_parallel(base, max_depth, index)
    _tree_index_cache[key] = index
    return index


def _scan_tree_parallel(base: Path, max_depth: int, index: dict):
    """
    Scan a directory tree using parallel threads for subdirectories.

    Strategy:
      1. List the top-level directory (files + subdirs)
      2. Add top-level files to the index
      3. Scan each subdirectory in parallel using ThreadPoolExecutor
      4. Merge all results into the shared index

    On a network share with 50+ subdirectories, this can be 5-10x
    faster than sequential scanning.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        entries = list(os.scandir(str(base)))
    except OSError:
        return

    # Add files from the top level
    subdirs = []
    for entry in entries:
        if entry.is_file():
            stem, ext = os.path.splitext(entry.name)
            if ext.lower() in EXTENSIONS:
                normalized = stem.strip().lower()
                if normalized not in index:
                    index[normalized] = Path(entry.path)
        elif entry.is_dir() and max_depth > 0:
            subdirs.append(Path(entry.path))

    if not subdirs:
        return

    # Scan subdirectories in parallel
    def _scan_subdir(subdir):
        local_index = {}
        _scan_tree_sequential(subdir, max_depth - 1, local_index)
        return local_index

    # Use up to 16 threads — network I/O bound, more threads = more
    # concurrent directory listings
    n_workers = min(16, len(subdirs))

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_scan_subdir, sd): sd for sd in subdirs}
        for future in as_completed(futures):
            try:
                local = future.result()
                for stem, path in local.items():
                    if stem not in index:
                        index[stem] = path
            except Exception:
                pass


def _scan_tree_sequential(directory: Path, depth: int, index: dict):
    """Recursively scan directory tree, adding files to index dict."""
    try:
        entries = list(os.scandir(str(directory)))
    except OSError:
        return

    for entry in entries:
        if entry.is_file():
            stem, ext = os.path.splitext(entry.name)
            if ext.lower() in EXTENSIONS:
                normalized = stem.strip().lower()
                if normalized not in index:
                    index[normalized] = Path(entry.path)
        elif entry.is_dir() and depth > 0:
            _scan_tree_sequential(Path(entry.path), depth - 1, index)


def clear_tree_index(base: str = None):
    """Clear the tree index cache. Call when files may have changed."""
    if base is None:
        _tree_index_cache.clear()
    else:
        _tree_index_cache.pop(str(base), None)


# ═════════════════════════════════════════════════════════════
# FLAT DIRECTORY RESOLVER
# ═════════════════════════════════════════════════════════════

def _resolve_flat(test_id: str, base: Path) -> Path | None:
    """Find a detail file in a flat (non-year) directory."""
    if not base.is_dir():
        return None

    # 1. Exact match
    for ext in EXTENSIONS:
        candidate = base / f"{test_id}{ext}"
        if candidate.exists():
            return candidate

    # 2. Fuzzy match (case-insensitive, whitespace-tolerant)
    fuzzy = _fuzzy_find(test_id, base)
    if fuzzy:
        return fuzzy

    # 3. Loose prefix match
    prefix = _get_search_prefix(test_id)
    if prefix and prefix != test_id:
        matches = _glob_prefix(base, prefix)
        if matches:
            return _best_match(test_id, matches)

    return None


# ═════════════════════════════════════════════════════════════
# FUZZY FILE FINDER
# ═════════════════════════════════════════════════════════════

# Cache of {directory_path_str: {normalized_stem: actual_filename}}
_dir_cache = {}


def _build_dir_cache(directory: Path) -> dict:
    """
    Scan a directory and build a lookup of normalized filename stems
    to actual filenames. Cached per directory.

    Normalization: strip whitespace, lowercase, remove extension.
    """
    dir_str = str(directory)
    if dir_str in _dir_cache:
        return _dir_cache[dir_str]

    cache = {}
    try:
        for entry in os.scandir(dir_str):
            if not entry.is_file():
                continue
            name = entry.name
            stem, ext = os.path.splitext(name)
            if ext.lower() not in EXTENSIONS:
                continue
            normalized = stem.strip().lower()
            # Keep the higher-priority extension if there's a collision
            if normalized not in cache:
                cache[normalized] = name
            else:
                existing_ext = os.path.splitext(cache[normalized])[1].lower()
                new_ext = ext.lower()
                ext_rank = {e: i for i, e in enumerate(EXTENSIONS)}
                if ext_rank.get(new_ext, 99) < ext_rank.get(existing_ext, 99):
                    cache[normalized] = name
    except (OSError, FileNotFoundError):
        pass

    _dir_cache[dir_str] = cache
    return cache


def clear_file_cache(directory: str = None):
    """
    Clear the directory file cache. Call if files have been added/removed.

    Args:
        directory: Specific directory to clear. If None, clears all.
    """
    if directory is None:
        _dir_cache.clear()
    else:
        _dir_cache.pop(str(directory), None)


def _fuzzy_find(test_id: str, directory: Path) -> Path | None:
    """
    Find a detail file using fuzzy matching.

    Handles:
      - Case mismatches
      - Whitespace in test_id or filename
      - Extension differences (.xlsm, .xlsx, .xls)

    NOTE: This does NOT do substring/contains matching, to avoid
    false positives when scanning directory trees. Use only for
    single-directory lookups where a near-exact match is expected.

    Args:
        test_id:   Value from the index's test_id column
        directory: Folder to search

    Returns:
        Path to the matched file, or None if not found.
    """
    if not test_id or not directory:
        return None

    normalized_id = str(test_id).strip().lower()
    cache = _build_dir_cache(directory)

    # 1. Exact normalized match
    if normalized_id in cache:
        return directory / cache[normalized_id]

    # 2. Try without all whitespace (embedded spaces)
    no_space_id = normalized_id.replace(" ", "")
    for norm_stem, fname in cache.items():
        if norm_stem.replace(" ", "") == no_space_id:
            return directory / fname

    return None


# ═════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════

def _row_value(row, field: str):
    """
    Safely extract a value from a row (pandas Series, dict, etc.).
    Returns None if the field is missing or the value is NaN/empty.
    """
    if row is None:
        return None

    val = None
    if hasattr(row, 'get'):
        val = row.get(field)
    elif hasattr(row, '__getitem__'):
        try:
            val = row[field]
        except (KeyError, IndexError):
            return None

    if val is None:
        return None

    # Handle pandas NaN
    try:
        if isinstance(val, float) and math.isnan(val):
            return None
    except (TypeError, ValueError):
        pass

    s = str(val).strip()
    return s if s else None


def _get_search_prefix(test_id: str) -> str:
    """
    Get the core prefix of a test ID for loose matching.

    Test IDs often end with a letter variant (P for production,
    D for development/debug, etc.).  Strip the trailing letter
    to get the common prefix.

    Examples:
        "99272055410010A2P" → "99272055410010A2"
        "99272055410010A2D" → "99272055410010A2"
        "12345"             → "1234"
    """
    if not test_id:
        return ""

    if (len(test_id) >= 2 and
            test_id[-1].isalpha() and
            test_id[-2].isdigit()):
        return test_id[:-1]

    if (len(test_id) >= 2 and
            test_id[-1].isalpha() and
            test_id[-2].isalpha()):
        return test_id[:-1]

    if len(test_id) > 4:
        return test_id[:-1]

    return test_id


def _glob_prefix(directory: Path, prefix: str) -> list[Path]:
    """
    Find all files in a directory whose name starts with the prefix
    and has a valid extension.
    """
    results = []
    prefix_lower = prefix.lower()

    try:
        for entry in os.scandir(str(directory)):
            if not entry.is_file():
                continue
            name = entry.name
            name_lower = name.lower()
            stem, ext = os.path.splitext(name_lower)
            if stem.startswith(prefix_lower) and ext in EXTENSIONS:
                results.append(Path(entry.path))
    except OSError:
        pass

    return results


def _best_match(test_id: str, candidates: list[Path]) -> Path:
    """
    Pick the best match from a list of candidate files.

    Prefers:
      1. Exact stem match
      2. Shortest filename (closest to the test ID)
      3. .xlsm over .xlsx over .xls
    """
    tid_lower = test_id.lower()

    for c in candidates:
        if c.stem.lower() == tid_lower:
            return c

    ext_priority = {".xlsm": 0, ".xlsx": 1, ".xls": 2}
    candidates.sort(key=lambda p: (
        len(p.stem),
        ext_priority.get(p.suffix.lower(), 9),
    ))

    return candidates[0]


@lru_cache(maxsize=4)
def _get_year_folders(base: Path) -> list[Path]:
    """
    List all 4-digit year folders under base, sorted descending.
    Cached because we may call this many times during a comparison.
    """
    year_dirs = []
    try:
        for entry in os.scandir(str(base)):
            if entry.is_dir() and re.match(r'^\d{4}$', entry.name):
                year_dirs.append(Path(entry.path))
    except OSError:
        pass

    year_dirs.sort(key=lambda p: p.name, reverse=True)
    return year_dirs


def _extract_year(row) -> int | None:
    """Extract the year from a test_date value in an index row."""
    if row is None:
        return None

    date_val = None
    if hasattr(row, 'get'):
        date_val = row.get("test_date")
    elif hasattr(row, '__getitem__'):
        try:
            date_val = row["test_date"]
        except (KeyError, IndexError):
            pass

    if date_val is None:
        return None

    date_str = str(date_val).strip()

    m = re.match(r'^(\d{4})-', date_str)
    if m:
        return int(m.group(1))

    m = re.search(r'(\d{4})', date_str)
    if m:
        year = int(m.group(1))
        if 1990 <= year <= 2099:
            return year

    return None