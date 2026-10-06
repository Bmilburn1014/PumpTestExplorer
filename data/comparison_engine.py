# data/comparison_engine.py
#
# Orchestrates the full comparison workflow:
#
#   1. Load PX base curves file
#   2. Classify pump type from curve number → pick correct index
#   3. Search index for matching tests within date range
#   4. Read trim/diameter info from index for each matching test
#   5. Group tests by trim diameter
#   6. Match each trim group to the nearest PX baseline curve
#   7. Apply affinity laws if trim ≠ baseline diameter exactly
#   8. Return structured results ready for charting
#
# Vertical pump trim handling:
#   The index "Top Impeller trim" column contains one or more stage
#   groups in the format:
#       (count) @ upper x lower [filing info]
#   Examples:
#       "(9) @ 7.06 x 7.06 Fig.1 to 0.125 As Cast"
#       "(5) @ 7.22 x 7.22 Fig.1 to 0.125 As Cast (4) @ 7.06 x 7.06 ..."
#
#   The trim diameter is the weighted average across all groups:
#       avg = sum(count * (upper + lower) / 2) / total_stages
#
#   PX baseline curves are per-stage. Multi-stage test data has
#   cumulative head and power, so these are divided by number of
#   stages before comparison.
#   TODO: Replace simple stage division with per-model correction
#         factor dictionary.
#
# Debug logging:
#   All debug info is collected in self._trace (a list of strings)
#   during processing — this is essentially free (just list.append).
#   After the comparison completes, _write_debug_log() dumps
#   everything to a timestamped file in the logs/ directory.
#   The status bar (_log) only receives high-level progress messages.

import os
import re
import numpy as np
import pandas as pd
from datetime import datetime
from dataclasses import dataclass, field

from data.px_curves import read_px_curves, PXCurveSet, TrimCurve
from data.pump_classifier import (
    classify_pump, PumpClassification,
    build_model_search_pattern, extract_pump_size,
)
from data.affinity import (
    scale_test_to_baseline, estimate_deviation_warning,
    compute_specific_speed, dicmas_exponents,
)
from data.loader import DataSource
from config import INDEX_FILES, ENVIRONMENT, DETAIL_DIRS, DETAIL_FILE_DIR, BASE_DIR
from pathlib import Path


# ── FTS Packing / Bearing Loss Configuration ─────────────────
# Used for vertical pumps when column BA in the detail file is
# zero (i.e. loss was not computed at test time).
#
# Formula (per row):
#   contact_area = packing_xsec × 0.94 × shaft_dia × π
#   loss_hp = (μ × contact_area × 0.8 × P_psi × RPM × (shaft_dia/2 / 12)) / 5252
#
# Speed and discharge pressure are read from the already-loaded
# DataFrame ('speed' and 'tdh_bowl' columns). Only column BA and
# shaft diameter require opening the raw Excel file.
#
# Packing cross section:
#   shaft_dia < 1.0"  →  0.375"
#   shaft_dia >= 1.0"  →  0.500"
#
# Change PACKING_FRICTION_COEFF as needed.
PACKING_FRICTION_COEFF = 0.1
FTS_LOSS_COL = "BA"          # Column letter in TEST DATA sheet
FTS_SHAFT_DIA_CELL = "B198"  # Cell in NEW ORDER INFO sheet
FTS_SHAFT_DIA_SHEET = "NEW ORDER INFO"


# ── Status bar logging (lightweight, user-facing) ────────────
def _log(msg: str):
    """Write a progress message to the status bar ring buffer."""
    try:
        from data.log_buffer import log as _log_impl
        _log_impl(msg)
    except ImportError:
        pass


@dataclass
class TestMatch:
    """A single test that matched the search criteria."""
    test_id: str
    pump_model: str
    trim_diameter: float        # From index — avg of upper & lower shroud
    test_date: str
    rpm: float
    rated_flow: float
    rated_head: float
    pass_fail: str
    source_index: str           # Which index file it came from

    num_stages: int = 1         # Number of stages (from trim string or index)
    filing_info: str = ""       # Impeller filing info, e.g. "Fig.1 to 0.125"

    # Conical impeller diameters (vertical pumps only)
    upper_diameter: float = None   # Shroud-side (larger) diameter
    lower_diameter: float = None   # Hub-side (smaller) diameter

    # Polished impeller flag (vertical pumps — detected by "##RA" in trim)
    is_polished: bool = False

    # Impeller part number field to be used by vertical pumps only
    impeller_part_number: str = None

    # Vertical Trim metadata
    mixed_impellers: bool = False
    mixed_trim: bool = False

    # After detail data is loaded:
    raw_flow: np.ndarray = None
    raw_head: np.ndarray = None
    raw_power: np.ndarray = None
    raw_efficiency: np.ndarray = None

    # After affinity scaling:
    scaled_flow: np.ndarray = None
    scaled_head: np.ndarray = None
    scaled_power: np.ndarray = None
    scaled_efficiency: np.ndarray = None  # Same as raw (efficiency is dimensionless)
    affinity_warning: str = None


@dataclass
class TrimGroup:
    """A group of tests at the same (or similar) impeller trim and speed."""
    nominal_diameter: float     # The grouped trim diameter
    baseline_trim: TrimCurve = None   # Nearest PX baseline curve
    diameter_ratio: float = 1.0       # baseline / actual
    tests: list = field(default_factory=list)  # List of TestMatch
    affinity_applied: bool = False
    baseline_speed: float = 0.0       # PX rated speed for this group
    avg_test_speed: float = 0.0       # Mean RPM of grouped tests
    speed_ratio: float = 1.0          # baseline_speed / avg_test_speed


@dataclass
class ComparisonResult:
    """Full result of a PX curve comparison."""
    px_curves: PXCurveSet = None
    classification: PumpClassification = None
    trim_groups: list = field(default_factory=list)  # List of TrimGroup
    search_params: dict = field(default_factory=dict)
    total_tests_found: int = 0
    total_tests_with_data: int = 0
    warnings: list = field(default_factory=list)


# ── Debug log file location ──────────────────────────────────
_LOG_DIR = str(BASE_DIR / "logs")


class ComparisonEngine:
    """
    Runs the full PX curve → test data comparison workflow.
    """
#========================================================================
# Initialization and Logging setup
#========================================================================
    def __init__(self):
        self.data_source = DataSource()
        self._last_diagnostics = []
        self._trace = []  # Debug trace — collected during run, written to file at end

    def _t(self, msg: str):
        """Append a line to the debug trace. Essentially free."""
        self._trace.append(msg)

    def _write_debug_log(self):
        """Write the collected debug trace to a timestamped log file."""
        try:
            os.makedirs(_LOG_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(_LOG_DIR, f"comparison_debug_{ts}.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"Comparison debug log — {datetime.now().isoformat()}\n")
                f.write("=" * 70 + "\n\n")
                for line in self._trace:
                    f.write(line + "\n")
            _log(f"Debug log written: {path}")
        except Exception as e:
            _log(f"Failed to write debug log: {e}")

#=========================================================================
# Helper Functions
#=========================================================================
    def _find_col(self, df, candidates):
        """
        Find a column by checking several likely raw or normalized names.
        Keeps this tolerant of Excel/header naming differences.
        """
        if df is None or df.empty:
            return None

        normalized = {
            str(c).strip().lower().replace(" ", "_"): c
            for c in df.columns
        }

        for cand in candidates:
            key = str(cand).strip().lower().replace(" ", "_")
            if key in normalized:
                return normalized[key]

        return None

    def _normalize_impeller_part(self, value):
        """
        Normalize impeller part numbers from Excel/index data.

        Handles:
        - None / NaN-like values
        - numeric Excel values like 504291814.0
        - extra spaces
        """
        if value is None:
            return ""

        text = str(value).strip()

        if not text or text.lower() in ("nan", "none", "null"):
            return ""

        # Convert Excel numeric strings like '504291814.0' -> '504291814'
        if text.endswith(".0"):
            left = text[:-2]
            if left.isdigit():
                return left

        return text

    def _extract_impeller_part_series(self, index_df):
        """
        Return a Series of impeller part numbers from the filtered vertical index.

        Prefer direct impeller part columns. If the index does not have one,
        fall back to parsing the model/curve string with classify_pump().
        """
        part_col = self._find_col(index_df, [
            "imp_part_num",
            "Imp Part Num",
            "imp_part_no",
            "Imp Part No",
            "imp_part_number",
            "Imp Part Number",

            "impeller_part_number",
            "impeller_part",
            "impeller part number",
            "Impeller Part Number",
            "Impeller Part",
            "part_number",
            "Part Number",
            "impeller part no",
            "Impeller Part No",
            "impeller_part_no",
            "part_no",
            "Part No",
            "impeller p/n",
            "Impeller P/N",
        ])

        if part_col is not None:
            return index_df[part_col].apply(self._normalize_impeller_part)

        model_col = self._find_col(index_df, [
            "pump_model",
            "model",
            "curve",
            "curve_number",
            "Pump Model",
            "Model",
            "Curve Number",
        ])

        if model_col is None:
            return None

        return index_df[model_col].apply(
            lambda value: self._normalize_impeller_part(
                classify_pump(str(value)).impeller_part
            )
        )


    def _filter_vertical_impeller_parts(
        self,
        index_df,
        classification,
        impeller_part_numbers,
    ):
        """
        Add vertical impeller part numbers to the index dataframe and,
        if selected values exist, filter vertical pumps to those parts only.

        This should run after model/date/test-iteration filtering.
        """
        if index_df is None or index_df.empty:
            return index_df

        if classification.pump_type not in ("vertical", "VT"):
            self._t("Impeller part filter skipped: pump is not vertical.")
            return index_df

        # Work on a copy so adding the helper column is safe.
        index_df = index_df.copy()

        part_series = self._extract_impeller_part_series(index_df)

        if part_series is None:
            self._t(
                "Impeller part filter skipped: no impeller part or model column found."
            )
            index_df["_impeller_part_number"] = ""
            return index_df

        # Keep this column for the TestMatch append block.
        index_df["_impeller_part_number"] = part_series.apply(
            self._normalize_impeller_part
            )

        selected = {
            self._normalize_impeller_part(p)
            for p in (impeller_part_numbers or [])
            if self._normalize_impeller_part(p)
            }

        if not selected:
            self._t(
                "Impeller part filter skipped: no part numbers selected. "
                "Part numbers were still extracted for match records."
            )
            return index_df

        before = len(index_df)

        filtered_df = index_df[
            index_df["_impeller_part_number"].isin(selected)
        ].copy()

        after = len(filtered_df)

        self._t(
            f"Applied vertical impeller part filter: "
            f"{before} rows -> {after} rows. Selected={sorted(selected)}"
        )

        return filtered_df
    
    def _row_get_by_candidates(self, row, candidates):
        """
        Return the first row value found from possible column names.
        Handles normalized and raw Excel column names.
        """
        if row is None:
            return None

        row_keys = {
            str(k).strip().lower().replace(" ", "_").replace(".", ""): k
            for k in row.index
        }

        for candidate in candidates:
            key = str(candidate).strip().lower().replace(" ", "_").replace(".", "")
            actual_col = row_keys.get(key)

            if actual_col is not None:
                return row.get(actual_col)

        return None


    def _detect_mixed_trim(self, row, classification):
        """
        Detect vertical tests with multiple impeller trim groups.
        Returns True if multiple trim groups are found.
        """
        if classification.pump_type not in ("vertical", "vt"):
            return False

        raw_trim = self._row_get_by_candidates(row, [
            "impeller_trim",
            "top_impeller_trim",
            "top impeller trim",
            "Top Impeller Trim",
        ])

        if raw_trim is None:
            return False

        trim_text = str(raw_trim).strip()

        if not trim_text or trim_text.lower() in ("nan", "none", "null"):
            return False

        # Detect repeated groups like:
        # (2) @ 11.88 x 12.16 ... (1) @ 10.55 x 11.35 ...
        group_matches = re.findall(r'\(\s*\d+\s*\)\s*@', trim_text)

        if len(group_matches) >= 2:
            return True

        # Fallback: count multiple diameter pairs.
        pair_matches = re.findall(
            r'\d+(?:\.\d+)?\s*x\s*\d+(?:\.\d+)?',
            trim_text,
            flags=re.IGNORECASE,
        )

        return len(pair_matches) >= 2
    
    def _normalize_yes_no(self, value) -> bool:
        """
        Return True only for values that clearly mean Yes.

        Handles:
        - Yes, Y, True, 1
        - lowercase/uppercase variants
        - numeric 1 or 1.0
        - ignores blanks, NaN, None, No, False, 0
        """
        if value is None:
            return False

        text = str(value).strip().lower()

        if not text or text in ("nan", "none", "null"):
            return False

        # Handle Excel numeric values like 1.0
        if text.endswith(".0"):
            left = text[:-2]
            if left.isdigit():
                text = left

        return text in ("yes", "y", "true", "t", "1")


    def _filter_mixed_impellers(self, df, classification):
        """
        Filter out vertical tests where Mixed Impellers is Yes.

        This is different from mixed_trim:
        mixed_impellers = exclude the row from comparison
        mixed_trim      = keep the row but show MT badge in trim card
        """
        if df is None or df.empty:
            return df

        if classification.pump_type not in ("vertical", "vt"):
            self._t("Mixed impellers filter skipped: pump is not vertical.")
            return df

        mixed_col = self._find_col(df, [
            "mixed_impellers",
            "Mixed Impellers",
            "mixed impellers",
            "mixed_impeller",
            "Mixed Impeller",
        ])

        if mixed_col is None:
            self._t("Mixed impellers filter skipped: column not found.")
            return df

        before = len(df)

        mixed_mask = df[mixed_col].apply(self._normalize_yes_no)

        filtered_df = df[~mixed_mask].copy()

        after = len(filtered_df)

        self._t(
            f"Mixed impellers filter applied using '{mixed_col}': "
            f"{before} rows -> {after} rows. Removed={before - after}"
        )

        return filtered_df
    
#=================================================================================
# Main Function
#=================================================================================

    def run_comparison(
        self,
        px_file_path: str,
        date_start: str = None,       # "YYYY-MM-DD" or None
        date_end: str = None,         # "YYYY-MM-DD" or None
        trim_tolerance_pct: float = 5.0,  # % width for trim grouping
        test_iteration: int = 1,      # Which iteration of the test to use
        head_col: str = "tdh_bowl",   # Which head column from detail
        power_col: str = "power_bowl_motor",  # Which power column
        max_tests: int = 2000,         # Limit for performance
        progress_callback=None,
        impeller_part_numbers=None,
    ) -> ComparisonResult:
        """
        Execute the full comparison pipeline.
        """
        # Reset trace for this run
        self._trace = []
        t = self._t
        impeller_part_numbers = [
            str(p).strip()
            for p in (impeller_part_numbers or [])
            if str(p).strip()
        ]
        t(f"Selected impeller part numbers: {impeller_part_numbers or None}")

        result = ComparisonResult()
        result.search_params = {
            "px_file": px_file_path,
            "date_start": date_start,
            "date_end": date_end,
            "trim_tolerance_pct": trim_tolerance_pct,
            "test_iteration": test_iteration,
            "head_col": head_col,
            "power_col": power_col,
        }

        t("=" * 70)
        t("  COMPARISON RUN")
        t("=" * 70)
        t(f"  px_file:       {px_file_path}")
        t(f"  date_start:    {date_start}")
        t(f"  date_end:      {date_end}")
        t(f"  tolerance:     {trim_tolerance_pct}%")
        t(f"  test_iteration:{test_iteration}")
        t(f"  head_col:      {head_col}")
        t(f"  power_col:     {power_col}")
        t("")

        # ── Step 1: Load PX curves ──────────────────────────
        if progress_callback:
            progress_callback("Step 1/6 — Loading PX curves...", 5)
        _log("Loading PX curves...")

        result.px_curves = read_px_curves(px_file_path)
        if not result.px_curves.trims:
            result.warnings.append("No trim curves found in PX file.")
            self._write_debug_log()
            return result

        t("=" * 70)
        t("  PX CURVES")
        t("=" * 70)
        t(f"  Curve number: {result.px_curves.curve_number}")
        t(f"  Rated speed:  {result.px_curves.rated_speed}")
        t(f"  Trim count:   {len(result.px_curves.trims)}")
        for trim in result.px_curves.trims:
            try:
                attrs = [a for a in dir(trim) if not a.startswith('_')]
                t(f"    Trim dia={trim.diameter}  attrs={attrs}")
            except Exception as e:
                t(f"    Trim logging error: {e}")
        t("")

        n_trims = len(result.px_curves.trims)
        _log(f"PX loaded — {n_trims} trims, "
             f"speed={result.px_curves.rated_speed} RPM")

        # ── Step 2: Classify pump type ──────────────────────
        result.classification = classify_pump(
            result.px_curves.curve_number
        )
        cls = result.classification

        t("=" * 70)
        t("  CLASSIFICATION")
        t("=" * 70)
        t(f"  pump_type:       {cls.pump_type}")
        t(f"  index_name:      {cls.index_name}")
        t(f"  trim_column:     {cls.trim_column}")
        t(f"  bowl_diameter:   {cls.bowl_diameter}")
        t(f"  detail_path:     {cls.detail_path}")
        t(f"  base_model_name: {cls.base_model_name}")
        t(f"  impeller_part:   {cls.impeller_part}")
        t("")

        _log(f"Classified: {cls.pump_type} → {cls.index_name}")

        # ── Step 2b: Force-resync index files ────────────────
        if progress_callback:
            progress_callback("Step 2/6 — Syncing index files...", 10)
        _log("Syncing index files...")
        self.data_source.sync.sync_index_files()

        # ── Step 3: Search index for matching tests ─────────
        if progress_callback:
            progress_callback("Step 3/6 — Searching index...", 15)
        _log("Searching index for matching tests...")

        matches = self._search_index(
            cls, result.px_curves,
            date_start, date_end,test_iteration, impeller_part_numbers=impeller_part_numbers,
        )

        print("Matches in run comparison are: ", matches)

        result.total_tests_found = len(matches)
        idx_name = cls.index_name
        _log(f"Index searched: {idx_name} — {len(matches)} tests found")
        if progress_callback:
            progress_callback(
                f"Step 3/6 — {len(matches)} matching tests found", 20)

        if not matches:
            result.warnings.append("No matching tests found in index.")
            if self._last_diagnostics:
                for d in self._last_diagnostics:
                    result.warnings.append(f"  ↳ {d}")
            self._write_debug_log()
            return result

        if len(matches) > max_tests:
            result.warnings.append(
                f"Found {len(matches)} tests, limiting to {max_tests}. "
                f"Narrow date range or increase limit."
            )
            matches = matches[:max_tests]

        # ── Step 4: Group by trim diameter ──────────────────
        if progress_callback:
            progress_callback(
                f"Step 4/6 — Grouping {len(matches)} tests by trim & speed...",
                25,
            )
        _log(f"Grouping {len(matches)} tests by trim diameter...")

        result.trim_groups = self._group_by_trim(
            matches, result.px_curves, trim_tolerance_pct, result.classification.pump_type
        )

        grouped_count = sum(len(g.tests) for g in result.trim_groups)
        excluded = len(matches) - grouped_count
        n_groups = len(result.trim_groups)

        group_msg = (f"{grouped_count} tests in {n_groups} group(s) "
                     f"within {trim_tolerance_pct}% tolerance")
        if excluded > 0:
            group_msg += f" — {excluded} excluded"
        _log(group_msg)
        if progress_callback:
            progress_callback(f"Step 4/6 — {group_msg}", 30)

        # ── Step 5: Load detail data and apply affinity ─────
        total = grouped_count
        _log(f"Loading detail data for {total} tests...")
        from data.path_resolver import build_detail_path

        detail_subdir = cls.detail_path
        is_vertical = cls.pump_type in ("vertical", "vt")

        # 5a. Resolve paths for all tests
        #     Optimizations:
        #       - Skip cached tests entirely (no path resolution needed)
        #       - Pre-warm the tree index ONCE with parallel scanning
        #       - Pass impeller_part for targeted VT subfolder lookup
        if progress_callback:
            progress_callback(
                f"Step 5/6 — Locating detail files for {total} tests...",
                32)
        _log(f"Locating detail files for {total} tests...")

        # Collect test IDs, separating cached from uncached
        all_resolved_paths = {}
        uncached_paths = {}
        cached_count = 0
        tests_needing_paths = []

        for group in result.trim_groups:
            for test in group.tests:
                if self.data_source.is_detail_cached(test.test_id):
                    cached_count += 1
                    t(f"  [PATH] {test.test_id} → CACHED (skip resolve)")
                else:
                    tests_needing_paths.append(test)

        _log(f"{cached_count} tests already cached, "
             f"{len(tests_needing_paths)} need path resolution")

        if tests_needing_paths:
            # Pre-warm the VT tree index BEFORE the per-test loop.
            # This scans the network directory ONCE (with parallel
            # subdirectory scanning) instead of per-test.
            if is_vertical and detail_subdir:
                base_dir = Path(DETAIL_DIRS.get(
                    cls.index_name, DETAIL_FILE_DIR)) / detail_subdir
                if base_dir.is_dir():
                    if progress_callback:
                        progress_callback(
                            f"Step 5/6 — Indexing {detail_subdir}/ "
                            f"directory...", 33)
                    _log(f"Pre-indexing directory: {base_dir}")
                    from data.path_resolver import prewarm_tree_index
                    n_files = prewarm_tree_index(base_dir)
                    _log(f"Directory indexed: {n_files} detail files found")
                    if progress_callback:
                        progress_callback(
                            f"Step 5/6 — Indexed {n_files} files, "
                            f"resolving {len(tests_needing_paths)} "
                            f"tests...", 35)

            # Build a richer row dict for targeted VT lookups
            imp_part = cls.impeller_part if cls else None

            for test in tests_needing_paths:
                row_info = {
                    "test_date": test.test_date,
                    "impeller_part_number": imp_part,
                    "rated_flow": test.rated_flow,
                    "num_stages": test.num_stages,
                }
                path = build_detail_path(
                    test.test_id,
                    row=row_info,
                    index_name=test.source_index,
                    detail_subdir=detail_subdir,
                )
                path_exists = path.exists() if path else False
                t(f"  [PATH] {test.test_id} → {path}  "
                  f"exists={path_exists}")

                if path and path_exists:
                    all_resolved_paths[test.test_id] = path
                    uncached_paths[test.test_id] = path

        need_read = len(uncached_paths)
        found = len(all_resolved_paths)
        t(f"  {cached_count} cached, {found} found, {need_read} to copy")
        _log(f"Files located: {found} found, {cached_count} cached, "
             f"{need_read} to copy")

        # 5b. Parallel-copy uncached files to local temp (8 threads)
        local_paths = {}
        if need_read > 0:
            from data.excel_reader import batch_copy_to_local
            network_list = list(uncached_paths.values())

            if progress_callback:
                progress_callback(
                    f"Step 5/6 — Copying {need_read} files from network...",
                    35)
            _log(f"Copying {need_read} files from network to local cache...")
            path_map = batch_copy_to_local(network_list)

            for tid, net_path in uncached_paths.items():
                local_paths[tid] = path_map.get(str(net_path),
                                                 str(net_path))

        # Build complete path map (local copy preferred, network fallback)
        all_file_paths = {}
        for tid, np_ in all_resolved_paths.items():
            all_file_paths[tid] = str(np_)  # network path as default
        for tid, lp in local_paths.items():
            all_file_paths[tid] = lp  # local copy overrides

        # 5c. Read detail data (from local copies or cache)
        t("")
        t("=" * 70)
        t("  DETAIL DATA + SCALING")
        t("=" * 70)

        processed = 0

        for group in result.trim_groups:
            t(f"\n  ── Group: PX baseline={group.nominal_diameter:.4f}, "
              f"{len(group.tests)} tests, "
              f"affinity={group.affinity_applied} ──")

            for test in group.tests:
                processed += 1
                pct = int(40 + (processed / max(total, 1)) * 50)
                if progress_callback:
                    progress_callback(
                        f"Step 5/6 — Loading & scaling test "
                        f"{processed}/{total}...",
                        pct)

                local = local_paths.get(test.test_id)
                if local and not self.data_source.is_detail_cached(
                        test.test_id):
                    self._read_and_cache_local(test, local)

                self._load_and_scale_test(
                    test, group, result.px_curves,
                    head_col, power_col,
                    is_vertical=is_vertical,
                    detail_file_path=all_file_paths.get(test.test_id),
                )

                if test.scaled_flow is not None:
                    result.total_tests_with_data += 1

        _log(f"Detail data loaded — {result.total_tests_with_data}/{total} "
             f"tests with data")
        if progress_callback:
            progress_callback(
                f"Step 6/6 — Finalizing "
                f"({result.total_tests_with_data} tests with data)...",
                95)

        # ── Summary ──────────────────────────────────────────
        t("")
        t("=" * 70)
        t("  SUMMARY")
        t("=" * 70)
        t(f"  Total tests found:     {result.total_tests_found}")
        t(f"  Tests with data:       {result.total_tests_with_data}")
        t(f"  Groups:                {len(result.trim_groups)}")
        t(f"  Detail: {cached_count} cached, {need_read} from Excel")

        failed = total - result.total_tests_with_data
        if failed > 0:
            result.warnings.append(
                f"{failed} of {total} tests had no detail data "
                f"(file not found or no usable columns). "
                f"Check debug log for details."
            )

        # Write the debug log file
        self._write_debug_log()

        if progress_callback:
            progress_callback("Comparison complete.", 100)

        return result

    # ── Internal methods ─────────────────────────────────────

    def _search_index(
        self,
        classification: PumpClassification,
        px_curves: PXCurveSet,
        date_start: str,
        date_end: str,
        test_iteration: int,
        impeller_part_numbers=None,
    ) -> list[TestMatch]:
        """
        Search the correct index file for matching pump tests.
        """
        t = self._t
        diag = []

        impeller_part_numbers = {
            str(p).strip()
            for p in (impeller_part_numbers or [])
            if str(p).strip()
        }

        t("=" * 70)
        t("  INDEX SEARCH")
        t("=" * 70)

        # ── Sanitize dates ───────────────────────────────────
        if date_start and not str(date_start).strip():
            date_start = None
        if date_end and not str(date_end).strip():
            date_end = None
        if (date_start and date_end and
                str(date_start).strip() == str(date_end).strip()):
            diag.append(f"Start == End ({date_start}) — skipping date filter")
            date_start = None
            date_end = None

        index_name = classification.index_name

        # Check that the source index file actually exists
        import os
        idx_path = None
        for cfg in INDEX_FILES:
            if cfg["name"] == index_name:
                idx_path = cfg["path"]
                break
        if idx_path:
            path_exists = os.path.isfile(idx_path)
            diag.append(f"Index file path: {idx_path}")
            diag.append(f"File exists: {path_exists}")
            if not path_exists:
                diag.append(
                    f"⚠ INDEX FILE NOT FOUND — check the path in "
                    f"config.py under ENVIRONMENT='{ENVIRONMENT}'. "
                    f"If this is a network path, verify VPN/drive access.")

        df = self.data_source.get_index_data(index_name)
        diag.append(f"Index '{index_name}': {len(df)} rows loaded")

        if df.empty:
            df_all = self.data_source.get_index_data()
            if idx_path and not os.path.isfile(idx_path):
                diag.append(
                    f"Index file not found at: {idx_path}")
                diag.append(
                    f"ENVIRONMENT is set to '{ENVIRONMENT}' in config.py. "
                    f"Update the path for '{index_name}' or switch "
                    f"ENVIRONMENT back to 'dev' if running from source.")
            else:
                diag.append(
                    f"Index '{index_name}' is empty. "
                    f"All indices combined: {len(df_all)} rows.")
            for d in diag:
                t(f"  {d}")
            self._last_diagnostics = diag
            return []

        diag.append(f"Columns: "
                    f"{[c for c in df.columns if not str(c).startswith('Unnamed')]}")

        # Filter by pump model pattern
        model_pattern = build_model_search_pattern(
            px_curves.curve_number
        )
        diag.append(f"Model search regex: {model_pattern}")

        pre_filter_count = len(df)

        if "pump_model" in df.columns:
            models = df["pump_model"].dropna().astype(str).unique()
            diag.append(f"Unique pump_model values: {len(models)}")
            sample = sorted(models)[:8]
            diag.append(f"Sample models: {sample}")

            mask = df["pump_model"].astype(str).str.contains(
                model_pattern, case=False, na=False, regex=True,
            )
            df_filtered = df[mask]
            diag.append(f"After model filter: {len(df_filtered)} rows "
                       f"(from {pre_filter_count})")

            # Progressive prefix fallback for vertical pumps:
            # If the full model letters (e.g. "HXBLC") match nothing,
            # try shorter prefixes ("HXBL", "HXB", "HX") until we get
            # a match. This handles unknown variant suffixes without
            # requiring them in _KNOWN_BASE_MODELS.
            if (df_filtered.empty
                    and classification.pump_type in ("vertical", "vt")
                    and classification.base_model_name):
                letters = classification.base_model_name
                if classification.perf_suffix:
                    letters = letters + classification.perf_suffix
                t(f"  [FALLBACK] Full model '{letters}' matched 0 rows "
                  f"— trying shorter prefixes")
                diag.append(f"Full model '{letters}' matched 0 rows "
                           f"— trying prefix fallback")

                for trim_len in range(len(letters) - 1, 1, -1):
                    prefix = letters[:trim_len]
                    import re as _re
                    fallback_pat = rf'^\s*{_re.escape(prefix)}[A-Za-z]?\s*$'
                    fb_mask = df["pump_model"].astype(str).str.contains(
                        fallback_pat, case=False, na=False, regex=True,
                    )
                    fb_count = fb_mask.sum()
                    t(f"  [FALLBACK] prefix='{prefix}' → "
                      f"regex '{fallback_pat}' → {fb_count} rows")
                    if fb_count > 0:
                        df_filtered = df[fb_mask]
                        model_pattern = fallback_pat
                        diag.append(
                            f"Prefix fallback '{prefix}' matched "
                            f"{fb_count} rows")
                        break

            df = df_filtered
        else:
            diag.append(
                "*** 'pump_model' column NOT FOUND — skipping model filter.")

        if df.empty:
            for d in diag:
                t(f"  {d}")
            self._last_diagnostics = diag
            return []

        # ── Bowl size filter ─────────────────────────────────
        if (classification.pump_type in ("vertical", "vt")
                and classification.bowl_diameter is not None):
            bowl = classification.bowl_diameter
            bowl_col = None
            for candidate in ("bowl_size", "bowl_dia", "bowl_diameter",
                              "bowl"):
                if candidate in df.columns:
                    bowl_col = candidate
                    break

            if bowl_col:
                pre_bowl = len(df)
                bowl_vals = pd.to_numeric(
                    df[bowl_col].astype(str).str.strip(),
                    errors="coerce",
                )
                df = df[bowl_vals == bowl]
                diag.append(f"After bowl size filter ({bowl_col}={bowl}): "
                           f"{len(df)} rows (from {pre_bowl})")
            else:
                diag.append(
                    "*** Bowl size column NOT FOUND — skipping bowl filter.")

            if df.empty:
                for d in diag:
                    t(f"  {d}")
                self._last_diagnostics = diag
                return []

        # ── Date range filter ────────────────────────────────
        if date_start or date_end:
            pre_date = len(df)

            if "test_date" in df.columns:
                raw_dates = df["test_date"].dropna().head(5).tolist()
                diag.append(f"Sample test_date values: {raw_dates}")

            effective_start, effective_end = date_start, date_end
            if date_start and date_end:
                try:
                    if pd.to_datetime(date_start) > pd.to_datetime(date_end):
                        effective_start, effective_end = date_end, date_start
                        diag.append("Dates were backwards — auto-swapped")
                except Exception:
                    pass

            df = self._filter_by_date(df, date_start, date_end)
            diag.append(f"After date filter [{effective_start} to "
                       f"{effective_end}]: {len(df)} rows (from {pre_date})")
        else:
            diag.append("No date filter applied")

        if df.empty:
            for d in diag:
                t(f"  {d}")
            self._last_diagnostics = diag
            return []

        # ── First test filter ────────────────────────────────
        pre_first = len(df)
        df = self._filter_test_iterations(df, test_iteration)
        diag.append(f"After test iteration filter: {len(df)} rows "
                   f"(from {pre_first})")
        
        # ── Filter Mixed Hydraulic Assemblies ────────────────────────────────
        pre_mixed = len(df)

        df = self._filter_mixed_impellers(
            df=df,
            classification=classification,
        )

        diag.append(
            f"After mixed impellers filter: {len(df)} rows "
            f"(from {pre_mixed})"
        )

        if df.empty:
            for d in diag:
                t(f"  {d}")
            self._last_diagnostics = diag
            return []
                
        # ── Vertical impeller part number filter ─────────────────
        pre_impeller = len(df)

        df = self._filter_vertical_impeller_parts(
            index_df=df,
            classification=classification,
            impeller_part_numbers=impeller_part_numbers,
        )

        diag.append(
            f"After vertical impeller part filter: {len(df)} rows "
            f"(from {pre_impeller})"
        )

        if df.empty:
            for d in diag:
                t(f"  {d}")
            self._last_diagnostics = diag
            return []

        # Log search diagnostics
        for d in diag:
            t(f"  {d}")

        # ── Trim extraction ──────────────────────────────────
        trim_col = classification.trim_column
        is_vertical = classification.pump_type in ("vertical", "vt")

        t(f"\n  Trim column: '{trim_col}'  "
          f"found={trim_col in df.columns}  is_vertical={is_vertical}")

        if trim_col in df.columns:
            raw_trims = df[trim_col].dropna().head(5).tolist()
            t(f"  Raw trim samples:")
            for i, rt in enumerate(raw_trims):
                t(f"    [{i}] type={type(rt).__name__}  value='{rt}'")

        t("")
        t("-" * 70)
        t("  PER-ROW TRIM PARSING")
        t("-" * 70)

        matches = []
        skipped_no_trim = 0

        for _, row in df.iterrows():
            test_id = str(row.get("test_id", "")).strip()
            if not test_id or test_id == "nan":
                continue

            raw_trim_val = row.get(trim_col)
            t(f"\n  test_id={test_id}")
            t(f"    raw trim: type={type(raw_trim_val).__name__}  "
              f"value='{raw_trim_val}'")

            trim_val, num_stages, filing_info, upper_dia, lower_dia, is_polished = \
                self._extract_trim_diameter(
                    row, trim_col, is_vertical=is_vertical,
                )

            t(f"    → trim_diameter={trim_val}, num_stages={num_stages}, "
              f"filing='{filing_info}'")
            if upper_dia and lower_dia:
                t(f"    → upper={upper_dia:.4f}, lower={lower_dia:.4f}, "
                  f"conical={'YES' if abs(upper_dia - lower_dia) > 0.001 else 'no'}")
            if is_polished:
                t(f"    → POLISHED impeller detected")

            if trim_val is None or trim_val <= 0:
                t(f"    *** SKIPPED (invalid trim)")
                skipped_no_trim += 1
                continue

            # Fallback: stages from index column
            if num_stages == 1 and "number_of_stages" in df.columns:
                idx_stages = row.get("number_of_stages")
                if idx_stages is not None:
                    try:
                        s = int(float(str(idx_stages)))
                        if s > 0:
                            t(f"    stages fallback: {num_stages} → {s} "
                              f"(from 'number_of_stages' column)")
                            num_stages = s
                    except (ValueError, TypeError):
                        pass

            # Also try num_stages column
            if num_stages == 1 and "num_stages" in df.columns:
                idx_stages = row.get("num_stages")
                if idx_stages is not None:
                    try:
                        s = int(float(str(idx_stages)))
                        if s > 0:
                            t(f"    stages fallback: {num_stages} → {s} "
                              f"(from 'num_stages' column)")
                            num_stages = s
                    except (ValueError, TypeError):
                        pass

            rpm_val = float(row.get("rpm", 0) or 0)
            model_val = str(row.get("pump_model", ""))

            mixed_impellers = False
            mixed_col = self._find_col(df, [
                "mixed_impellers",
                "Mixed Impellers",
                "mixed impellers",
            ])

            if mixed_col is not None:
                mixed_impellers = self._normalize_yes_no(row.get(mixed_col))

            mixed_trim = self._detect_mixed_trim(
                row=row,
                classification=classification,
            )

            if mixed_trim:
                t(f"    MT badge detected from impeller_trim: {row.get('impeller_trim', '')}")

            matches.append(TestMatch(
                test_id=test_id,
                pump_model=model_val,
                trim_diameter=trim_val,
                test_date=str(row.get("test_date", "")),
                rpm=rpm_val,
                rated_flow=float(row.get("rated_flow", 0) or 0),
                rated_head=float(row.get("rated_head", 0) or 0),
                pass_fail=str(row.get("pass_fail", "")),
                source_index=classification.index_name,
                num_stages=num_stages,
                filing_info=filing_info,
                upper_diameter=upper_dia,
                lower_diameter=lower_dia,
                is_polished=is_polished,
                mixed_trim=mixed_trim,
                mixed_impellers=mixed_impellers,
                impeller_part_number=str(row.get("_impeller_part_number", "") or "").strip(),
            ))

            t(f"    ✓ trim={trim_val:.4f}, stages={num_stages}, "
              f"rpm={rpm_val}, model='{model_val}', filing='{filing_info}'")

        t(f"\n  Total matches: {len(matches)}, "
          f"skipped (no trim): {skipped_no_trim}")

        if matches:
            trims = sorted(set(m.trim_diameter for m in matches))
            t(f"  Unique trims: {[f'{v:.4f}' for v in trims[:15]]}")
            stages = sorted(set(m.num_stages for m in matches))
            t(f"  Unique stages: {stages}")

        self._last_diagnostics = diag
        return matches

    def _filter_test_iterations(self, df, test_iteration):
        """Filter dataframe by selected test iteration."""
        if "test_run" not in df.columns:
            return df

        run_col = df["test_run"]

        # Try numeric interpretation first
        numeric_runs = pd.to_numeric(run_col, errors="coerce")

        if numeric_runs.notna().sum() > len(df) * 0.5:
            if test_iteration == 1:
                return df[numeric_runs == 1]

            elif test_iteration == 2:
                return df[numeric_runs <= 2]

            else:  # 3+
                return df

        # Text-based values
        str_runs = run_col.astype(str).str.strip().str.lower()

        first_patterns = {"1", "1st", "first", "a"}
        second_patterns = {"2", "2nd", "second", "b"}

        if test_iteration == 1:
            mask = (
                str_runs.isin(first_patterns)
            |   str_runs.str.startswith("1")
            )

        elif test_iteration == 2:
            mask = (
                str_runs.isin(second_patterns)
            |   str_runs.str.startswith("2")
            )

        else:  # 3+
            mask = str_runs.str.extract(r"(\d+)")[0].astype(float) >= 3

        return df[mask.fillna(False)]
    

    def _filter_by_date(self, df: pd.DataFrame,
                        date_start: str, date_end: str) -> pd.DataFrame:
        """Filter DataFrame by date range on the test_date column."""
        if "test_date" not in df.columns:
            return df

        if date_start and date_end:
            try:
                s = pd.to_datetime(date_start)
                e = pd.to_datetime(date_end)
                if s > e:
                    date_start, date_end = date_end, date_start
            except (ValueError, TypeError):
                pass

        raw = df["test_date"].astype(str).str.strip()
        dates = pd.to_datetime(raw, format="%Y-%m-%d", errors="coerce")
        has_date = dates.notna()
        mask = pd.Series(True, index=df.index)

        if date_start:
            try:
                start = pd.to_datetime(date_start)
                mask &= (~has_date) | (dates >= start)
            except (ValueError, TypeError):
                pass

        if date_end:
            try:
                end = pd.to_datetime(date_end)
                mask &= (~has_date) | (dates <= end)
            except (ValueError, TypeError):
                pass

        return df[mask]

    # ── Trim diameter extraction ─────────────────────────────

    def _extract_trim_diameter(self, row, trim_col: str,
                               is_vertical: bool = False) -> tuple:
        """
        Extract the impeller trim diameter from an index row.

        Returns:
            (trim_diameter, num_stages, filing_info,
             upper_diameter, lower_diameter, is_polished)
        """
        t = self._t
        val = row.get(trim_col)
        if val is None or str(val).strip() in ("", "nan"):
            t(f"      [extract_trim] empty/nan → (None, 1, '')")
            return None, 1, "", None, None, False

        val_str = str(val).strip()
        has_at = "@" in val_str

        # Detect polished impeller: number followed by RA/Ra (e.g. "75RA")
        is_polished = bool(re.search(r'\d+\s*[Rr][Aa]', val_str))
        if is_polished:
            t(f"      [extract_trim] POLISHED detected in '{val_str}'")

        t(f"      [extract_trim] is_vertical={is_vertical}  has_@={has_at}")

        # Vertical: "(stages) @ upper x lower" format
        if is_vertical and has_at:
            t(f"      [extract_trim] → _parse_vertical_trim")
            avg, stages, filing, upper, lower = self._parse_vertical_trim(
                val_str)
            return avg, stages, filing, upper, lower, is_polished

        if is_vertical and not has_at:
            t(f"      [extract_trim] *** is_vertical but no '@' — "
              f"falling through to generic")

        # Simple numeric
        try:
            numeric = float(val_str)
            t(f"      [extract_trim] float → {numeric}")
            return numeric, 1, "", None, None, is_polished
        except ValueError:
            pass

        # "upper x lower" without stages
        m = re.search(r'(\d+\.?\d*)\s*x\s*(\d+\.?\d*)', val_str)
        if m:
            upper = float(m.group(1))
            lower = float(m.group(2))
            avg = (upper + lower) / 2.0
            t(f"      [extract_trim] x-format: ({upper}+{lower})/2 = {avg}")
            return avg, 1, "", upper, lower, is_polished

        numbers = re.findall(r'\d+\.?\d+', val_str)
        if numbers:
            result = max(float(n) for n in numbers)
            t(f"      [extract_trim] number fallback → {result}")
            return result, 1, "", None, None, is_polished

        t(f"      [extract_trim] FAILED → (None, 1, '')")
        return None, 1, "", None, None, False

    def _parse_vertical_trim(self, val_str: str) -> tuple:
        """
        Parse a vertical pump trim string with one or more stage groups.

        Also extracts the impeller filing info (e.g. "Fig.1 to 0.125")
        and returns the upper/lower diameters from the first group
        (for Dicmas conical impeller correction).

        Calculation:
            avg_diameter = sum(count * (upper + lower) / 2) / total_stages

        Returns:
            (avg_diameter, total_stages, filing_info,
             upper_diameter, lower_diameter)
        """
        t = self._t
        if not val_str:
            return None, 1, "", None, None

        t(f"      [parse_vt] input='{val_str}'")

        groups = re.findall(
            r'\((\d+)\)\s*@\s*(\d+\.?\d*)\s*x\s*(\d+\.?\d*)',
            val_str,
        )

        t(f"      [parse_vt] regex matched {len(groups)} group(s)")

        if not groups:
            t(f"      [parse_vt] *** NO MATCH")
            return None, 1, "", None, None

        weighted_sum = 0.0
        total_stages = 0
        # Track weighted-average upper and lower across all groups
        weighted_upper = 0.0
        weighted_lower = 0.0

        for count_str, upper_str, lower_str in groups:
            count = int(count_str)
            upper = float(upper_str)
            lower = float(lower_str)
            group_avg = (upper + lower) / 2.0
            contrib = count * group_avg

            t(f"        ({count}) @ {upper} x {lower}  "
              f"avg={group_avg:.4f}  "
              f"contrib={count}×{group_avg:.4f}={contrib:.4f}")

            weighted_sum += contrib
            weighted_upper += count * upper
            weighted_lower += count * lower
            total_stages += count

        if total_stages == 0:
            return None, 1, "", None, None

        avg_diameter = weighted_sum / total_stages
        avg_upper = weighted_upper / total_stages
        avg_lower = weighted_lower / total_stages

        t(f"      [parse_vt] RESULT: "
          f"{weighted_sum:.4f} / {total_stages} = {avg_diameter:.4f}")
        t(f"      [parse_vt] upper={avg_upper:.4f}  lower={avg_lower:.4f}")

        # Extract filing info: "Fig.N to X.XXX" from the first group
        filing_match = re.search(
            r'(Fig\.?\s*\d+\.?\s*to\s*\d+\.?\d*)',
            val_str, re.IGNORECASE,
        )
        filing_info = filing_match.group(1).strip() if filing_match else ""

        t(f"      [parse_vt] filing_info='{filing_info}'")

        return avg_diameter, total_stages, filing_info, avg_upper, avg_lower

    # ── Trim grouping ────────────────────────────────────────

    def _group_by_trim(
        self,
        matches: list[TestMatch],
        px_curves: PXCurveSet,
        tolerance_pct: float,
        pump_type: str,
    ) -> list[TrimGroup]:
        """Group tests by nearest PX baseline trim AND speed."""
        t = self._t

        if not matches or not px_curves.trims:
            return []

        px_diameters = sorted(
            [tr.diameter for tr in px_curves.trims], reverse=True
        )
        baseline_speed = (px_curves.rated_speed
                          if px_curves.rated_speed > 0 else 0.0)

        t("")
        t("=" * 70)
        t("  TRIM GROUPING")
        t("=" * 70)
        t(f"  PX baseline diameters: {px_diameters}")
        t(f"  PX baseline speed:     {baseline_speed}")
        t(f"  Tolerance:             {tolerance_pct}%")
        t(f"  Input tests:           {len(matches)}")
        t("")

        # ── Speed pre-filter ─────────────────────────────────
        speed_excluded = 0
        speed_filtered = []

        for m in matches:
            if baseline_speed > 0 and m.rpm > 0:
                speed_pct = (abs(m.rpm - baseline_speed)
                             / baseline_speed * 100)
                print("pump classification is:", pump_type, flush=True)
                if pump_type in ["VT", "Vertical", "vertical"]:
                    print("pump in vertical case")
                    if speed_pct < 50 and speed_pct > 120:
                        print("vertical pump outside HI tolerance excluded", flush=True)
                        t(f"  [SPEED EXCLUDE] {m.test_id}: "
                        f"rpm={m.rpm:.1f}, diff={speed_pct:.2f}%")
                        speed_excluded += 1
                        continue
                else:
                    print("pump in else case")
                    if speed_pct > tolerance_pct:
                        t(f"  [SPEED EXCLUDE] {m.test_id}: "
                         f"rpm={m.rpm:.1f}, diff={speed_pct:.2f}%")
                        speed_excluded += 1
                        continue
            speed_filtered.append(m)

        t(f"  Speed filter: {len(speed_filtered)} kept, "
          f"{speed_excluded} excluded")
        t("")

        # ── Assign to nearest PX trim ────────────────────────
        groups = {
            dia: TrimGroup(
                nominal_diameter=dia,
                baseline_trim=px_curves.get_trim(dia),
                diameter_ratio=1.0,
                baseline_speed=baseline_speed,
                affinity_applied=False,
                tests=[],
            )
            for dia in px_diameters
        }

        trim_excluded = 0
        for m in speed_filtered:
            nearest_dia = min(px_diameters,
                              key=lambda d: abs(d - m.trim_diameter))
            pct_diff = (abs(m.trim_diameter - nearest_dia)
                        / nearest_dia * 100)
            assigned = pct_diff <= tolerance_pct

            t(f"  [TRIM] {m.test_id}: "
              f"test_trim={m.trim_diameter:.4f} → "
              f"nearest_px={nearest_dia:.4f}  "
              f"diff={pct_diff:.2f}%  "
              f"{'ASSIGNED' if assigned else 'EXCLUDED'}")

            if assigned:
                groups[nearest_dia].tests.append(m)
            else:
                trim_excluded += 1

        if trim_excluded > 0:
            self._last_diagnostics.append(
                f"{trim_excluded} tests excluded (trim outside tolerance)")
        if speed_excluded > 0:
            self._last_diagnostics.append(
                f"{speed_excluded} tests excluded (RPM outside tolerance)")

        # ── Compute group ratios ─────────────────────────────
        t("")
        result = []
        for dia in px_diameters:
            grp = groups[dia]
            if grp.tests:
                trim_values = [tt.trim_diameter for tt in grp.tests]
                avg_trim = np.mean(trim_values)
                grp.diameter_ratio = dia / avg_trim if avg_trim > 0 else 1.0

                rpms = [tt.rpm for tt in grp.tests if tt.rpm > 0]
                grp.avg_test_speed = np.mean(rpms) if rpms else 0.0
                if baseline_speed > 0 and grp.avg_test_speed > 0:
                    grp.speed_ratio = baseline_speed / grp.avg_test_speed
                else:
                    grp.speed_ratio = 1.0

                # Always apply affinity scaling — even small differences
                # matter for curve comparison accuracy
                grp.affinity_applied = True

                t(f"  ── [GROUP] PX baseline={dia:.4f} ──")
                t(f"    tests:        {len(grp.tests)}")
                t(f"    trims:        {[f'{v:.4f}' for v in trim_values]}")
                t(f"    avg_trim:     {avg_trim:.4f}")
                t(f"    dia_ratio:    {dia:.4f} / {avg_trim:.4f} = "
                f"{grp.diameter_ratio:.6f}")
                t(f"    avg_rpm:      {grp.avg_test_speed:.1f}")
                t(f"    speed_ratio:  {baseline_speed:.1f} / "
                f"{grp.avg_test_speed:.1f} = {grp.speed_ratio:.6f}")

            else:
                t(f"   [GROUP EMPTY] PX={dia:.4f}: no tests")

            result.append(grp)

        return result

    # ── Detail data loading ──────────────────────────────────

    def _read_and_cache_local(self, test, local_path: str):
        """Read a detail file from local disk and cache the result."""
        from data.excel_reader import read_detail_file
        try:
            df = read_detail_file(local_path)
            if not df.empty:
                self.data_source.cache.store_dataframe(
                    f"detail_{test.test_id}", df,
                    source_path=local_path,
                )
        except Exception:
            pass

    def _load_and_scale_test(
        self,
        test: TestMatch,
        group: TrimGroup,
        px_curves: PXCurveSet,
        head_col: str,
        power_col: str,
        is_vertical: bool = False,
        detail_file_path: str = None,
    ):
        """
        Load detail data for a test, normalize multi-stage to single-stage,
        then apply affinity scaling if needed.
        """
        t = self._t

        t(f"\n  ── [LOAD+SCALE] {test.test_id} ──")
        t(f"    trim={test.trim_diameter:.4f}, "
          f"stages={test.num_stages}, rpm={test.rpm}, "
          f"filing='{test.filing_info}'")

        detail = self.data_source.get_detail_data(
            test.test_id,
            index_name=test.source_index,
            row_info={"test_date": test.test_date},
        )

        if detail.empty:
            t(f"    ✗ no detail data")
            return

        t(f"    columns: {list(detail.columns)}")
        t(f"    rows: {len(detail)}")

        if "flow" not in detail.columns:
            t(f"    ✗ no 'flow' column")
            return

        flow_series = pd.to_numeric(detail["flow"], errors="coerce")
        valid_mask = flow_series.notna() & (flow_series >= 0)

        flow = flow_series[valid_mask].values.astype(float)
        if len(flow) < 2:
            t(f"    ✗ only {len(flow)} valid flow points")
            return

        t(f"    flow: {len(flow)} pts")
        t(f"      values: {[f'{v:.2f}' for v in flow]}")

        head = None
        if head_col in detail.columns:
            h = pd.to_numeric(detail[head_col], errors="coerce")
            head = h[valid_mask].values.astype(float)
            t(f"    head ('{head_col}'): {len(head)} pts")
            t(f"      values: {[f'{v:.2f}' for v in head]}")
        else:
            t(f"    ✗ head column '{head_col}' not in columns")

        power = None
        actual_power_col = power_col
        if power_col in detail.columns:
            p = pd.to_numeric(detail[power_col], errors="coerce")
            p_vals = p[valid_mask].values.astype(float)
            # Check if dyno data is actually usable (not all zero/negative)
            if np.any(p_vals > 0):
                power = p_vals
                t(f"    power ('{power_col}'): {len(power)} pts")
                t(f"      values: {[f'{v:.4f}' for v in power]}")
            else:
                t(f"    power ('{power_col}'): all zero/negative — trying fallback")

        # Fallback: dyno → motor (or vice versa within same measurement point)
        if power is None:
            fallback_col = None
            if "dyno" in power_col:
                fallback_col = power_col.replace("dyno", "motor")
            elif "motor" in power_col:
                fallback_col = power_col.replace("motor", "dyno")

            if fallback_col and fallback_col in detail.columns:
                p = pd.to_numeric(detail[fallback_col], errors="coerce")
                p_vals = p[valid_mask].values.astype(float)
                if np.any(p_vals > 0):
                    power = p_vals
                    actual_power_col = fallback_col
                    t(f"    power FALLBACK ('{fallback_col}'): {len(power)} pts")
                    t(f"      values: {[f'{v:.4f}' for v in power]}")
                else:
                    t(f"    ✗ fallback power '{fallback_col}' also zero/negative")
            elif power_col not in detail.columns:
                t(f"    ✗ power column '{power_col}' not in columns")

        # Efficiency — derive column name from the power source actually used
        eff_col = actual_power_col.replace("power_", "eff_")
        efficiency = None
        if eff_col in detail.columns:
            e = pd.to_numeric(detail[eff_col], errors="coerce")
            efficiency = e[valid_mask].values.astype(float)
            t(f"    efficiency ('{eff_col}'): {len(efficiency)} pts")
            t(f"      values: {[f'{v:.2f}' for v in efficiency]}")
        else:
            # Fallback: try eff_overall
            if "eff_overall" in detail.columns:
                e = pd.to_numeric(detail["eff_overall"], errors="coerce")
                efficiency = e[valid_mask].values.astype(float)
                t(f"    efficiency ('eff_overall' fallback): {len(efficiency)} pts")
            else:
                t(f"    ✗ efficiency column '{eff_col}' not in columns")

        # ── FTS Packing / Bearing Loss (vertical pumps only) ─────
        # If column BA in the detail file is all zeros, the loss was
        # never computed at test time. We compute it per row and
        # subtract from power.
        if is_vertical and power is not None and detail_file_path:
            fts_result = _read_and_apply_fts_loss(
                detail_file_path, power, detail, valid_mask, t,
            )
            if fts_result is not None:
                t(f"    power BEFORE FTS: {[f'{v:.4f}' for v in power]}")
                power = fts_result
                t(f"    power AFTER FTS:  {[f'{v:.4f}' for v in power]}")

        # ── Stage normalization ──────────────────────────────
        # NOTE: Efficiency is dimensionless and does NOT change
        #       with number of stages, so it is not divided.
        n = test.num_stages
        if n > 1:
            t(f"    [STAGE NORM] {n}-stage → dividing head & power by {n}")
            if head is not None:
                t(f"      head BEFORE: {[f'{v:.2f}' for v in head]}")
                head = head / n
                t(f"      head AFTER:  {[f'{v:.2f}' for v in head]}")
            if power is not None:
                t(f"      power BEFORE: {[f'{v:.4f}' for v in power]}")
                power = power / n
                t(f"      power AFTER:  {[f'{v:.4f}' for v in power]}")
        else:
            t(f"    [STAGE NORM] single-stage — no division")

        test.raw_flow = flow
        test.raw_head = head
        test.raw_power = power
        test.raw_efficiency = efficiency

        # ── Affinity scaling ─────────────────────────────────
        # NOTE: Efficiency is dimensionless — it does NOT scale
        #       with affinity laws. We pair it with scaled_flow
        #       so the X axis aligns with head/power charts.
        if group.affinity_applied and group.baseline_trim:
            baseline_dia = group.baseline_trim.diameter

            t(f"    [AFFINITY]")
            t(f"      test_diameter:     {test.trim_diameter:.4f}")
            t(f"      baseline_diameter: {baseline_dia:.4f}")
            t(f"      dia_ratio:         "
              f"{baseline_dia:.4f}/{test.trim_diameter:.4f} = "
              f"{baseline_dia / test.trim_diameter:.6f}")
            t(f"      test_rpm:          {test.rpm}")
            t(f"      baseline_rpm:      {px_curves.rated_speed}")
            if test.rpm > 0 and px_curves.rated_speed > 0:
                t(f"      speed_ratio:       "
                  f"{px_curves.rated_speed:.1f}/{test.rpm:.1f} = "
                  f"{px_curves.rated_speed / test.rpm:.6f}")

            # Log Dicmas conical correction if applicable (vertical only)
            if (not is_vertical
                    and abs(baseline_dia / test.trim_diameter - 1.0) > 0.005):
                raw_d = baseline_dia / test.trim_diameter
                eff_d = 1.2 * raw_d - 0.2
                t(f"      HORIZONTAL slip factor: "
                  f"raw_d_ratio={raw_d:.6f}  "
                  f"eff_d_ratio=1.2×{raw_d:.4f}−0.2={eff_d:.6f}")

            # Compute specific speed for Dicmas (vertical conical only)
            ns_value = None
            is_conical = (test.upper_diameter and test.lower_diameter
                          and abs(test.upper_diameter - test.lower_diameter) > 0.001)
            if is_vertical and is_conical and group.baseline_trim:
                bt = group.baseline_trim
                bep_q = bt.bep_flow
                bep_h = None

                # Try metadata first
                if (bep_q and bep_q > 0
                        and bt.head_flow is not None
                        and bt.head is not None
                        and len(bt.head_flow) >= 2):
                    bep_h = float(np.interp(
                        bep_q, bt.head_flow, bt.head))
                    t(f"      BEP from PX metadata: "
                      f"Q={bep_q:.0f}  H={bep_h:.1f}")

                # Fallback: compute BEP from head + power curves
                if (bep_q is None or bep_q <= 0 or bep_h is None):
                    bep_q, bep_h = _compute_bep_from_curves(bt, t)

                ns_value = compute_specific_speed(
                    px_curves.rated_speed, bep_q, bep_h)

                t(f"      CONICAL impeller: upper={test.upper_diameter:.4f}  "
                  f"lower={test.lower_diameter:.4f}")
                if ns_value:
                    t(f"      Ns = {px_curves.rated_speed:.0f} × "
                      f"√{bep_q:.0f} / {bep_h:.1f}^0.75 = "
                      f"{ns_value:.0f}")
                else:
                    t(f"      Ns = could not compute "
                      f"(BEP data missing — no head or power curves)")
                if ns_value and ns_value > 1500:
                    q_e, h_e, p_e = dicmas_exponents(ns_value)
                    t(f"      Dicmas Fig 2.20 (Ns={ns_value:.0f}): "
                      f"q={q_e:.4f}  h={h_e:.4f}  p={p_e:.4f}")
                elif ns_value:
                    t(f"      Ns={ns_value:.0f} ≤ 1500 → standard exponents")

            scaled = scale_test_to_baseline(
                test_flow=flow,
                test_head=head,
                test_power=power,
                test_diameter=test.trim_diameter,
                baseline_diameter=baseline_dia,
                test_speed=test.rpm if test.rpm > 0 else None,
                baseline_speed=(px_curves.rated_speed
                                if px_curves.rated_speed > 0 else None),
                upper_diameter=test.upper_diameter,
                lower_diameter=test.lower_diameter,
                is_horizontal=not is_vertical,
                specific_speed=ns_value,
            )
            test.scaled_flow = scaled.flow
            test.scaled_head = scaled.head
            test.scaled_power = scaled.power
            test.scaled_efficiency = efficiency  # No scaling

            t(f"      scaled_flow:  {[f'{v:.2f}' for v in scaled.flow]}")
            if scaled.head is not None:
                t(f"      scaled_head:  {[f'{v:.2f}' for v in scaled.head]}")
            if scaled.power is not None:
                t(f"      scaled_power: {[f'{v:.4f}' for v in scaled.power]}")

            test.affinity_warning = estimate_deviation_warning(
                scaled.diameter_ratio, scaled.speed_ratio
            )
            if test.affinity_warning:
                t(f"      ⚠ {test.affinity_warning}")
        else:
            reason = []
            if not group.affinity_applied:
                reason.append("affinity_applied=False")
            if not group.baseline_trim:
                reason.append("baseline_trim=None")
            t(f"    [NO AFFINITY] {', '.join(reason)}")
            test.scaled_flow = flow
            test.scaled_head = head
            test.scaled_power = power
            test.scaled_efficiency = efficiency  # No scaling

        # ── Final ────────────────────────────────────────────
        t(f"    [FINAL] plotted_flow:  "
          f"[{test.scaled_flow[0]:.2f} → {test.scaled_flow[-1]:.2f}]")
        if test.scaled_head is not None:
            t(f"    [FINAL] plotted_head:  "
              f"[{test.scaled_head[0]:.2f} → {test.scaled_head[-1]:.2f}]")
        if test.scaled_power is not None:
            t(f"    [FINAL] plotted_power: "
              f"[{test.scaled_power[0]:.4f} → {test.scaled_power[-1]:.4f}]")
        if test.scaled_efficiency is not None:
            t(f"    [FINAL] plotted_eff:   "
              f"[{test.scaled_efficiency[0]:.2f} → {test.scaled_efficiency[-1]:.2f}]")
             

# =====================================================================
# BEP COMPUTATION FROM HEAD + POWER CURVES
# =====================================================================

def _compute_bep_from_curves(trim, t=None):
    """
    Compute BEP flow and BEP head from a TrimCurve's head and power
    arrays when the PX metadata fields (bep_flow, bep_efficiency)
    are missing.

    Uses:  η = (Q × H) / (3960 × P) × 100
    Finds the flow where η is maximized.

    Args:
        trim: TrimCurve with head_flow, head, power_flow, power arrays
        t: optional trace/log function

    Returns:
        (bep_flow, bep_head) or (None, None) if curves are insufficient
    """
    if t is None:
        t = lambda msg: None

    hf = trim.head_flow
    hv = trim.head
    pf = trim.power_flow
    pv = trim.power

    if (hf is None or hv is None or pf is None or pv is None
            or len(hf) < 2 or len(pf) < 2):
        t(f"      [BEP compute] insufficient curve data")
        return None, None

    hf = np.asarray(hf, dtype=float)
    hv = np.asarray(hv, dtype=float)
    pf = np.asarray(pf, dtype=float)
    pv = np.asarray(pv, dtype=float)

    # Find overlapping flow range
    flow_min = max(hf.min(), pf.min())
    flow_max = min(hf.max(), pf.max())
    if flow_min >= flow_max:
        t(f"      [BEP compute] no overlapping flow range")
        return None, None

    # Evaluate on a common flow grid (exclude zero flow)
    common_flow = np.linspace(max(flow_min, 1.0), flow_max, 100)
    head_interp = np.interp(common_flow, hf, hv)
    power_interp = np.interp(common_flow, pf, pv)

    # Compute efficiency
    with np.errstate(divide='ignore', invalid='ignore'):
        eff = np.where(
            power_interp > 0,
            (common_flow * head_interp) / (3960.0 * power_interp) * 100,
            0,
        )

    valid = (eff > 0) & (eff <= 100) & (common_flow > 0)
    if valid.sum() < 2:
        t(f"      [BEP compute] no valid efficiency points")
        return None, None

    # BEP = flow where efficiency is maximum
    bep_idx = np.argmax(eff[valid])
    bep_flow = float(common_flow[valid][bep_idx])
    bep_head = float(head_interp[valid][bep_idx])
    bep_eff = float(eff[valid][bep_idx])

    t(f"      [BEP compute] from curves: "
      f"Q={bep_flow:.0f} GPM  H={bep_head:.1f} ft  η={bep_eff:.1f}%")

    return bep_flow, bep_head

def _read_ba_values_xls(file_path: str,
                        sheet_name: str = "TEST DATA",
                        loss_col: str = "BA") -> list[float]:
    """
    Read the FTS loss column from a legacy .xls file using calamine.

    Returns:
        List of float values from column BA.
    """
    import pandas as pd
    from openpyxl.utils import column_index_from_string

    raw = pd.read_excel(
        file_path,
        sheet_name,
        engine="calamine",
        header=None,
    )

    col_idx = column_index_from_string(loss_col) - 1

    if col_idx >= raw.shape[1]:
        return []

    values = []

    for row_idx in range(1, len(raw)):
        val = raw.iat[row_idx, col_idx]

        try:
            values.append(
                float(val) if pd.notna(val) else 0.0
            )
        except (ValueError, TypeError):
            values.append(0.0)

    return values

def _read_shaft_diameter_xls(file_path, sheet_name, cell_ref):
    """
    Read a single cell (e.g. B12) from a legacy .xls file using calamine.

    Args:
        file_path: XLS file path
        sheet_name: e.g. "NEW ORDER INFO"
        cell_ref: e.g. "B12"

    Returns:
        Float shaft diameter or None.
    """
    from openpyxl.utils.cell import coordinate_to_tuple
    import pandas as pd

    raw = pd.read_excel(
        file_path,
        sheet_name=sheet_name,
        engine="calamine",
        header=None,
    )

    row_num, col_num = coordinate_to_tuple(cell_ref)

    row_idx = row_num - 1
    col_idx = col_num - 1

    if row_idx >= raw.shape[0]:
        return None

    if col_idx >= raw.shape[1]:
        return None

    value = raw.iat[row_idx, col_idx]

    try:
        return float(value)
    except (TypeError, ValueError):
        return None

# =====================================================================
# FTS PACKING / BEARING LOSS — vertical pumps only
# =====================================================================

def _read_and_apply_fts_loss(file_path, power, detail_df, valid_mask, t):
    """
    Read column BA from the detail file. If all zeros, compute
    the packing/bearing loss from speed, discharge pressure, and
    shaft diameter, then subtract it from the power array.

    Uses the already-loaded DataFrame for speed (column 'speed')
    and discharge pressure (column 'tdh_bowl' which is in PSI).
    Only opens the raw Excel to check column BA and read shaft
    diameter from NEW ORDER INFO.

    Corrected formula:
      contact_area = packing_xsec × 0.94 × shaft_dia × π
      loss_hp = (μ × contact_area × 0.8 × P_psi × RPM × (shaft_dia/2 / 12)) / 5252

    Where 5252 = 33000/(2π), the HP conversion from ft·lbf/min.

    Args:
        file_path:   Path to the .xlsm detail file
        power:       numpy array of power values (filtered by valid_mask)
        detail_df:   pandas DataFrame with all columns from TEST DATA
        valid_mask:  pandas boolean mask used to filter rows
        t:           trace/log function

    Returns:
        Corrected power array, or None if no correction needed.
    """
    import math
    wb = None
    t(f"    [FTS] Checking packing/bearing loss...")
    t(f"    [FTS] file: {file_path}")
    t(f"    [FTS] friction coefficient: {PACKING_FRICTION_COEFF}")
    ext = Path(file_path).suffix.lower()
    print(f"    [FTS] file extension: {ext}", flush=True)
    if ext == ".xls":
        print(f"    [FTS] Using calamine engine for legacy .xls file", flush=True)
        ba_vals = _read_ba_values_xls(
            file_path,
            sheet_name="TEST DATA",
            loss_col=FTS_LOSS_COL,
        )
        print(f"    [FTS] Read {len(ba_vals)} BA rows from TEST DATA", flush=True)
        shaft_dia = _read_shaft_diameter_xls(
            file_path,
            sheet_name=FTS_SHAFT_DIA_SHEET,
            cell_ref=FTS_SHAFT_DIA_CELL,
        )
        print(f"    [FTS] Read shaft diameter from {FTS_SHAFT_DIA_SHEET} "
              f"{FTS_SHAFT_DIA_CELL}: {shaft_dia}", flush=True)
    
        if shaft_dia is None:
            t(
                f"    [FTS] ✗ Shaft diameter cell "
                f"{FTS_SHAFT_DIA_CELL} invalid"
            )
            return None

    else:
        # existing openpyxl path
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True,
                                        data_only=True)
        except Exception as e:
            t(f"    [FTS] ✗ Cannot open workbook: {e}")
            return None

        try:
            ws = wb["TEST DATA"]
        except KeyError:
            t(f"    [FTS] ✗ Sheet 'TEST DATA' not found")
            if wb is not None:
                wb.close()
            return None

        # Read column BA (all rows) to check if loss was already computed
        from openpyxl.utils import column_index_from_string
        ba_col_idx = column_index_from_string(FTS_LOSS_COL)

        ba_vals = []
        for row in ws.iter_rows(min_row=2, values_only=False):
            if len(row) < ba_col_idx:
                continue
            cell_val = row[ba_col_idx - 1].value
            try:
                ba_vals.append(float(cell_val) if cell_val is not None else 0.0)
            except (ValueError, TypeError):
                ba_vals.append(0.0)

        t(f"    [FTS] Read {len(ba_vals)} BA rows from TEST DATA")
        t(f"    [FTS] BA values (first 10): {ba_vals[:10]}")

    non_zero_count = sum(1 for v in ba_vals if abs(v) > 0.0001)
    if non_zero_count > 0:
        t(f"    [FTS] BA has {non_zero_count} non-zero values — "
          f"loss already applied, skipping")
        if wb is not None:
            wb.close()
        return None

    t(f"    [FTS] BA is all zeros — computing packing loss")

    # ── Read line shaft diameter from NEW ORDER INFO ─────────
    if ext != ".xls":
        try:
            ws_info = wb[FTS_SHAFT_DIA_SHEET]
        except KeyError:
            t(f"    [FTS] ✗ Sheet '{FTS_SHAFT_DIA_SHEET}' not found")
            if wb is not None:
                wb.close()
            return None
    if ext != ".xls":
        try:
            shaft_cell = ws_info[FTS_SHAFT_DIA_CELL].value
        except KeyError:
            t(f"    [FTS] ✗ Cell '{FTS_SHAFT_DIA_CELL}' not found in sheet '{FTS_SHAFT_DIA_SHEET}'")    
            if wb is not None:
                wb.close()
            return None
    if ext != ".xls":
        try:
            shaft_dia = float(shaft_cell)
        except (ValueError, TypeError):
            t(f"    [FTS] ✗ Shaft diameter cell {FTS_SHAFT_DIA_CELL} "
            f"= '{shaft_cell}' — not a number")
            return None

    if shaft_dia < 0.25 or shaft_dia > 8.0:
        t(f"    [FTS] ⚠ Shaft diameter {shaft_dia:.3f}\" "
          f"outside expected range (0.25–8.0\") — skipping")
        return None

    t(f"    [FTS] Line shaft diameter: {shaft_dia:.4f}\"")

    # ── Get speed and discharge pressure from DataFrame ──────
    # Speed: 'speed' column (RPM)
    # Discharge pressure: 'tdh_bowl' column (PSI — before ft conversion)
    if "speed" not in detail_df.columns:
        t(f"    [FTS] ✗ 'speed' column not in DataFrame")
        return None

    speed_series = pd.to_numeric(detail_df["speed"], errors="coerce")
    speed_filtered = speed_series[valid_mask].values.astype(float)

    # Discharge pressure — tdh_bowl is in PSI
    press_col = "tdh_bowl"
    if press_col not in detail_df.columns:
        # Try tdh_pump as fallback
        press_col = "tdh_pump"
        if press_col not in detail_df.columns:
            t(f"    [FTS] ✗ No pressure column (tdh_bowl/tdh_pump) in DataFrame")
            return None
        t(f"    [FTS] Using '{press_col}' for discharge pressure (fallback)")
    else:
        t(f"    [FTS] Using '{press_col}' for discharge pressure")

    press_series = pd.to_numeric(detail_df[press_col], errors="coerce")
    press_filtered = press_series[valid_mask].values.astype(float)

    if len(speed_filtered) != len(power) or len(press_filtered) != len(power):
        t(f"    [FTS] ✗ Row count mismatch: speed={len(speed_filtered)} "
          f"press={len(press_filtered)} power={len(power)} — skipping")
        return None

    t(f"    [FTS] Speed (first 5): {[f'{v:.1f}' for v in speed_filtered[:5]]}")
    t(f"    [FTS] Pressure (first 5): {[f'{v:.2f}' for v in press_filtered[:5]]}")

    # ── Packing cross section ────────────────────────────────
    packing_xsec = 0.375 if shaft_dia < 1.0 else 0.5
    t(f"    [FTS] Packing cross section: {packing_xsec}\" "
      f"(shaft {'<' if shaft_dia < 1.0 else '>='} 1.0\")")

    # ── Contact area ─────────────────────────────────────────
    contact_area = packing_xsec * 0.94 * shaft_dia * math.pi
    t(f"    [FTS] Contact area: {packing_xsec} × 0.94 × "
      f"{shaft_dia:.4f} × π = {contact_area:.6f} in²")

    # ── Compute per-row loss (corrected formula) ─────────────
    # loss_hp = (μ × contact_area × 0.8 × P_psi × RPM × (D_shaft/2 / 12)) / 5252
    shaft_radius_ft = (shaft_dia / 2.0) / 12.0

    loss_hp = (PACKING_FRICTION_COEFF
               * contact_area
               * 0.8
               * press_filtered
               * speed_filtered
               * shaft_radius_ft
               ) / 5252.0

    t(f"    [FTS] shaft_radius_ft: {shaft_radius_ft:.6f}")
    t(f"    [FTS] Loss HP (first 5): {[f'{v:.6f}' for v in loss_hp[:5]]}")
    t(f"    [FTS] Loss HP range: {loss_hp.min():.6f} — {loss_hp.max():.6f}")

    # ── Subtract from power ──────────────────────────────────
    corrected_power = power - loss_hp

    t(f"    [FTS] Power correction applied:")
    t(f"      Original power (first 5): {[f'{v:.4f}' for v in power[:5]]}")
    t(f"      Loss HP       (first 5): {[f'{v:.6f}' for v in loss_hp[:5]]}")
    t(f"      Corrected     (first 5): {[f'{v:.4f}' for v in corrected_power[:5]]}")
    t(f"      Avg loss: {np.mean(loss_hp):.4f} HP")
    t(f"      Max loss: {np.max(loss_hp):.4f} HP")

    neg_count = np.sum(corrected_power < 0)
    if neg_count > 0:
        t(f"    [FTS] ⚠ {neg_count} points have negative corrected power!")

    return corrected_power

