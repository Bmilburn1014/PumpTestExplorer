# manual_mode/engine.py
"""
Manual mode engine.

Reuses existing functions:
  - ComparisonEngine._extract_trim_diameter() for VT trim parsing
  - scale_test_to_baseline() for affinity correction
  - prewarm_tree_index / batch_copy_to_local for fast file loading

New function:
  - filter_by_part_number() — replaces the PSD-derived part number
    with a user-provided input for vertical pump filtering
"""

import os
import re
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

from data.loader import DataSource
from data.path_resolver import build_detail_path
from data.excel_reader import read_detail_file
from data.affinity import scale_test_to_baseline, estimate_deviation_warning
from data.comparison_engine import ComparisonEngine
from config import DETAIL_DIRS, DETAIL_FILE_DIR, BASE_DIR


def _log(msg):
    try:
        from data.log_buffer import log as _impl
        _impl(msg)
    except ImportError:
        pass


_PUMP_TYPE_INDEX = {
    "inline": "ppu_test_log", "horizontal": "ppu_test_log",
    "end_suction": "ppu_test_log", "vertical": "new_test_list",
}

_LOG_DIR = str(BASE_DIR / "logs")


# ── New function: part number filter for manual mode ─────────
# In auto mode the part number comes from the PSD/PX file.
# In manual mode the user types it in.  This function applies
# the same column-search and contains-match logic as
# ComparisonEngine._search_index.

def filter_by_part_number(df: pd.DataFrame,
                          part_num: str) -> pd.DataFrame:
    """
    Filter index DataFrame to rows whose impeller part number
    column contains the given part number string.

    Searches the same candidate columns as auto mode.
    Returns the filtered DataFrame (may be empty).
    """
    if df.empty or not part_num or not part_num.strip():
        return df

    part_num = part_num.strip()

    # Same column candidates as comparison_engine._search_index
    part_col = None
    for candidate in ("imp_part_num", "top_impeller_part_number",
                      "impeller_part", "part_number", "part_no",
                      "impeller_pn", "material_1"):
        if candidate in df.columns:
            part_col = candidate
            break

    if not part_col:
        _log(f"Manual: part number column not found, skipping filter")
        return df

    mask = (df[part_col].astype(str).str.strip()
            .str.contains(re.escape(part_num), case=False,
                          na=False, regex=True))
    filtered = df[mask]
    _log(f"Manual: part number filter ({part_col}={part_num}): "
         f"{len(filtered)}/{len(df)} rows")
    return filtered


class ManualModeEngine:

    def __init__(self):
        self.data_source = DataSource()
        self._comp_engine = ComparisonEngine()
        self._trace = []

    def _t(self, msg: str):
        """Append a line to the debug trace."""
        self._trace.append(msg)

    def _write_debug_log(self):
        """Write the collected debug trace to a timestamped log file."""
        try:
            os.makedirs(_LOG_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(_LOG_DIR, f"manual_debug_{ts}.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"Manual mode debug log — "
                        f"{datetime.now().isoformat()}\n")
                f.write("=" * 70 + "\n\n")
                for line in self._trace:
                    f.write(line + "\n")
            _log(f"Manual debug log written: {path}")
        except Exception as e:
            _log(f"Failed to write manual debug log: {e}")

    def get_index_name(self, pt):
        return _PUMP_TYPE_INDEX.get(pt, "new_test_list")

    # ── INDEX QUERIES ────────────────────────────────────────

    def get_models(self, pump_type, date_start=None, date_end=None):
        df = self._load_index(pump_type)
        if df.empty or "pump_model" not in df.columns:
            return []
        df = self._filter_dates(df, date_start, date_end)
        if pump_type == "vertical" and "bowl_size" in df.columns:
            bowl = (pd.to_numeric(df["bowl_size"], errors="coerce")
                    .fillna(0).astype(int).astype(str))
            model = df["pump_model"].fillna("").astype(str).str.strip()
            combined = (bowl + model).loc[
                (model != "") & (model != "nan") & (bowl != "0")]
            return [{"label": m, "value": m}
                    for m in sorted(combined.unique()) if m.strip()]
        models = sorted(
            df["pump_model"].dropna().astype(str).str.strip()
            .loc[lambda s: (s != "") & (s != "nan")].unique())
        return [{"label": m, "value": m} for m in models if m.strip()]

    # ── MAIN COMPARISON ─────────────────────────────────────

    def run_manual_comparison(
        self, pump_type, model, *,
        bl_date_start=None, bl_date_end=None,
        bl_trim=None, bl_speed=None,
        bl_part_num=None,
        raw_date_start=None, raw_date_end=None,
        trim_tolerance_pct=3.0,
        head_col="tdh_bowl", power_col="power_bowl_dyno",
        progress_callback=None,
    ):
        self._trace = []
        t = self._t

        t("=" * 70)
        t("  MANUAL MODE COMPARISON RUN")
        t("=" * 70)
        t(f"  pump_type:       {pump_type}")
        t(f"  model:           {model}")
        t(f"  bl_date_start:   {bl_date_start}")
        t(f"  bl_date_end:     {bl_date_end}")
        t(f"  bl_trim:         {bl_trim}")
        t(f"  bl_speed:        {bl_speed}")
        t(f"  bl_part_num:     {bl_part_num}")
        t(f"  raw_date_start:  {raw_date_start}")
        t(f"  raw_date_end:    {raw_date_end}")
        t(f"  tolerance:       ±{trim_tolerance_pct}%")
        t(f"  head_col:        {head_col}")
        t(f"  power_col:       {power_col}")
        t("")

        _log("Manual mode: starting comparison…")

        # Ensure trim and speed are proper floats (Dash can pass
        # None, int, float, or even string from Input components)
        try:
            bl_trim = float(bl_trim) if bl_trim is not None else None
        except (ValueError, TypeError):
            bl_trim = None
        try:
            bl_speed = float(bl_speed) if bl_speed is not None else None
        except (ValueError, TypeError):
            bl_speed = None

        t(f"  Resolved: bl_trim={bl_trim} (type={type(bl_trim).__name__})  "
          f"bl_speed={bl_speed} (type={type(bl_speed).__name__})")

        index_name = self.get_index_name(pump_type)
        is_vertical = pump_type == "vertical"
        is_horizontal = not is_vertical  # matches auto mode
        trim_col = self._get_trim_column(pump_type)
        detail_subdir = model.strip() if is_vertical and model else None
        idx_df = self.data_source.get_index_data(index_name)

        t(f"  index_name:      {index_name}")
        t(f"  trim_col:        {trim_col}")
        t(f"  detail_subdir:   {detail_subdir}")
        t(f"  index rows:      {len(idx_df)}")
        t("")

        # ── Find baseline ────────────────────────────────────
        t("=" * 70)
        t("  BASELINE SEARCH")
        t("=" * 70)
        if progress_callback:
            progress_callback("Manual: finding baseline tests…", 5)
        bl_all = self._find_test_rows(
            pump_type, model, bl_date_start, bl_date_end, idx_df)
        t(f"  Model + date filter: {len(bl_all)} rows")

        if is_vertical and bl_part_num:
            pre_part = len(bl_all)
            bl_all = filter_by_part_number(bl_all, bl_part_num)
            t(f"  Part number filter ({bl_part_num}): "
              f"{len(bl_all)} rows (from {pre_part})")

        pre_tol = len(bl_all)
        bl_rows = self._filter_by_tolerance(
            bl_all, trim_col, bl_trim, bl_speed,
            trim_tolerance_pct, is_vertical)
        t(f"  Tolerance filter (±{trim_tolerance_pct}% of "
          f"trim={bl_trim}, speed={bl_speed}): "
          f"{len(bl_rows)} rows (from {pre_tol})")
        _log(f"Manual: {len(bl_rows)}/{len(bl_all)} baseline in tolerance")

        if "test_id" in bl_rows.columns:
            for _, row in bl_rows.head(20).iterrows():
                tid = str(row.get("test_id", ""))
                trim_val, nstg, *_ = self._comp_engine._extract_trim_diameter(
                    row, trim_col, is_vertical=is_vertical)
                rpm = row.get("rpm", "?")
                t(f"    BL: {tid}  trim={trim_val}  rpm={rpm}  "
                  f"stages={nstg}")
        t("")

        # ── Find raw ─────────────────────────────────────────
        t("=" * 70)
        t("  RAW SEARCH")
        t("=" * 70)
        raw_all = self._find_test_rows(
            pump_type, model, raw_date_start, raw_date_end, idx_df)
        t(f"  Model + date filter: {len(raw_all)} rows")

        if is_vertical and bl_part_num:
            pre_part = len(raw_all)
            raw_all = filter_by_part_number(raw_all, bl_part_num)
            t(f"  Part number filter ({bl_part_num}): "
              f"{len(raw_all)} rows (from {pre_part})")

        pre_tol = len(raw_all)
        raw_rows = self._filter_by_tolerance(
            raw_all, trim_col, bl_trim, bl_speed,
            trim_tolerance_pct, is_vertical)
        t(f"  Tolerance filter: {len(raw_rows)} rows (from {pre_tol})")
        _log(f"Manual: {len(raw_rows)}/{len(raw_all)} raw in tolerance")

        if "test_id" in raw_rows.columns:
            for _, row in raw_rows.head(20).iterrows():
                tid = str(row.get("test_id", ""))
                trim_val, nstg, *_ = self._comp_engine._extract_trim_diameter(
                    row, trim_col, is_vertical=is_vertical)
                rpm = row.get("rpm", "?")
                t(f"    RAW: {tid}  trim={trim_val}  rpm={rpm}  "
                  f"stages={nstg}")
        t("")

        # ── Batch load all detail files ──────────────────────
        t("=" * 70)
        t("  DETAIL FILE LOADING")
        t("=" * 70)
        all_rows = pd.concat([bl_rows, raw_rows]).drop_duplicates(
            subset=["test_id"] if "test_id" in bl_rows.columns else None)
        t(f"  Unique tests to load: {len(all_rows)}")

        if progress_callback:
            progress_callback(
                f"Manual: loading {len(all_rows)} detail files…", 10)

        detail_map = self._load_detail_optimized(
            all_rows, index_name, detail_subdir,
            is_vertical, progress_callback, 10, 50)
        t(f"  Detail files loaded: {len(detail_map)}")

        bl_tids = set(bl_rows["test_id"].astype(str).str.strip()
                      ) if "test_id" in bl_rows.columns else set()
        raw_tids = set(raw_rows["test_id"].astype(str).str.strip()
                       ) if "test_id" in raw_rows.columns else set()
        bl_detail = [(tid, d) for tid, d in detail_map if tid in bl_tids]
        raw_detail = [(tid, d) for tid, d in detail_map if tid in raw_tids]
        t(f"  Baseline with data: {len(bl_detail)}")
        t(f"  Raw with data:      {len(raw_detail)}")
        t("")

        # ── Scale + fit baseline ─────────────────────────────
        t("=" * 70)
        t("  AFFINITY SCALING")
        t("=" * 70)
        if progress_callback:
            progress_callback("Manual: scaling baseline…", 55)
        bl_scaled = self._scale_tests(
            bl_detail, bl_rows, trim_col, is_vertical,
            bl_trim, bl_speed, is_horizontal, head_col, power_col)
        t(f"  Baseline scaled: {len(bl_scaled)} tests")

        for s in bl_scaled:
            t(f"    BL scaled: {s['test_id']}  "
              f"flow_pts={len(s['scaled_flow']) if s['scaled_flow'] is not None else 0}  "
              f"warning={s.get('affinity_warning', '')[:60]}")

        if progress_callback:
            progress_callback("Manual: scaling comparison…", 70)
        raw_scaled = self._scale_tests(
            raw_detail, raw_rows, trim_col, is_vertical,
            bl_trim, bl_speed, is_horizontal, head_col, power_col)
        t(f"  Comparison scaled: {len(raw_scaled)} tests")

        for s in raw_scaled:
            t(f"    CMP scaled: {s['test_id']}  "
              f"flow_pts={len(s['scaled_flow']) if s['scaled_flow'] is not None else 0}  "
              f"warning={s.get('affinity_warning', '')[:60]}")
        t("")

        # ── Build two trim groups ────────────────────────────
        if progress_callback:
            progress_callback("Manual: building results…", 85)

        bl_entries = self._build_entries(
            bl_scaled, bl_rows, index_name, trim_col, is_vertical)
        raw_entries = self._build_entries(
            raw_scaled, raw_rows, index_name, trim_col, is_vertical)

        bl_label = f"Baseline {bl_trim}" if bl_trim else "Baseline"
        raw_label = f"Comparison {bl_trim}" if bl_trim else "Comparison"

        # Each group is a trim card — no fixed PX baseline curve,
        # the user controls both curve fits via shape tools.
        bl_group = {
            "nominal_diameter": bl_label,
            "baseline_diameter": bl_trim,
            "diameter_ratio": 1.0,
            "affinity_applied": True,
            "tests": bl_entries,
            "px_flow": None,
            "px_head": None,
            "px_power_flow": None,
            "px_power": None,
            "px_bep_flow": None,
            "px_bep_efficiency": None,
        }
        raw_group = {
            "nominal_diameter": raw_label,
            "baseline_diameter": bl_trim,
            "diameter_ratio": 1.0,
            "affinity_applied": True,
            "tests": raw_entries,
            "px_flow": None,
            "px_head": None,
            "px_power_flow": None,
            "px_power": None,
            "px_bep_flow": None,
            "px_bep_efficiency": None,
        }

        bl_with_data = sum(1 for e in bl_entries if e["has_data"])
        raw_with_data = sum(1 for e in raw_entries if e["has_data"])

        if progress_callback:
            progress_callback("Manual: done", 100)

        warnings = []
        if len(bl_rows) == 0 and len(bl_all) > 0:
            warnings.append(
                f"0/{len(bl_all)} baseline tests in tolerance.")
        elif not bl_scaled:
            warnings.append(f"No baseline files found for {model}.")
        if len(raw_rows) == 0 and len(raw_all) > 0:
            warnings.append(
                f"0/{len(raw_all)} comparison tests in tolerance. "
                f"Widen tolerance or adjust trim/speed.")
        elif not raw_scaled:
            warnings.append(f"No comparison files found for {model}.")

        # ── Summary ──────────────────────────────────────────
        t("=" * 70)
        t("  SUMMARY")
        t("=" * 70)
        t(f"  Baseline tests found:    {len(bl_all)}")
        t(f"  Baseline in tolerance:   {len(bl_rows)}")
        t(f"  Baseline with data:      {bl_with_data}")
        t(f"  Comparison tests found:  {len(raw_all)}")
        t(f"  Comparison in tolerance: {len(raw_rows)}")
        t(f"  Comparison with data:    {raw_with_data}")
        for w in warnings:
            t(f"  WARNING: {w}")

        self._write_debug_log()

        _log(f"Manual: done — {bl_with_data} bl, "
             f"{raw_with_data} cmp with data")

        return {
            "groups": [bl_group, raw_group],
            "total_found": len(bl_all) + len(raw_all),
            "total_with_data": bl_with_data + raw_with_data,
            "total_groups": 2,
            "warnings": warnings,
            "manual_mode": True,
            "baseline_test_count": bl_with_data,
            "baseline_speed": bl_speed,
            "baseline_trim": bl_trim,
        }

    # ── TOLERANCE FILTER (uses existing _extract_trim_diameter) ──

    def _filter_by_tolerance(self, df, trim_col, target_trim,
                              target_speed, tol_pct, is_vertical=False):
        if df.empty:
            return df
        mask = pd.Series(True, index=df.index)

        # Trim filter — use ComparisonEngine._extract_trim_diameter
        # to parse VT complex trim strings
        if target_trim and target_trim > 0 and trim_col in df.columns:
            trims = []
            for _, row in df.iterrows():
                trim_val, *_ = self._comp_engine._extract_trim_diameter(
                    row, trim_col, is_vertical=is_vertical)
                trims.append(trim_val if trim_val else float('nan'))
            trim_series = pd.Series(trims, index=df.index)
            band = target_trim * (tol_pct / 100)
            mask = mask & trim_series.between(
                target_trim - band, target_trim + band)

        # Speed filter
        if target_speed and target_speed > 0 and "rpm" in df.columns:
            speeds = pd.to_numeric(df["rpm"], errors="coerce")
            band = target_speed * (tol_pct / 100)
            mask = mask & speeds.between(
                target_speed - band, target_speed + band)

        return df[mask]

    # ── OPTIMIZED FILE LOADING ───────────────────────────────

    def _load_detail_optimized(self, test_rows, index_name,
                                detail_subdir, is_vertical,
                                progress_callback, pct_start, pct_end):
        t = self._t
        if test_rows.empty or "test_id" not in test_rows.columns:
            return []

        results = []
        uncached = []
        cached_count = 0

        for _, row in test_rows.iterrows():
            tid = str(row.get("test_id", "")).strip()
            if not tid or tid == "nan":
                continue
            if self.data_source.is_detail_cached(tid):
                df = self.data_source.cache.read_table(f"detail_{tid}")
                if df is not None and not df.empty:
                    results.append((tid, df))
                    cached_count += 1
                    continue
            uncached.append((tid, row))

        t(f"  {cached_count} cached, {len(uncached)} need loading")
        _log(f"Manual: {cached_count} cached, {len(uncached)} to load")

        if not uncached:
            return results

        # Pre-warm VT tree index (existing function)
        if is_vertical and detail_subdir:
            base = Path(DETAIL_DIRS.get(
                index_name, DETAIL_FILE_DIR)) / detail_subdir
            if base.is_dir():
                try:
                    from data.path_resolver import prewarm_tree_index
                    n = prewarm_tree_index(base)
                    t(f"  Pre-indexed {n} files in {detail_subdir}/")
                    _log(f"Manual: pre-indexed {n} files in {detail_subdir}/")
                except Exception as e:
                    t(f"  Prewarm failed: {e}")
                    _log(f"Manual: prewarm failed: {e}")

        # Resolve all paths
        resolved = {}
        for tid, row in uncached:
            row_info = {
                "test_date": str(row.get("test_date", "")),
                "rated_flow": row.get("rated_flow"),
                "num_stages": row.get("num_stages", 1),
            }
            path = build_detail_path(
                tid, row=row_info, index_name=index_name,
                detail_subdir=detail_subdir)
            path_exists = path.exists() if path else False
            t(f"  [PATH] {tid} → {path}  exists={path_exists}")
            if path and path_exists:
                resolved[tid] = path

        t(f"  Paths resolved: {len(resolved)}/{len(uncached)}")
        _log(f"Manual: {len(resolved)}/{len(uncached)} paths resolved")
        if not resolved:
            return results

        # Batch copy from network (existing function)
        if progress_callback:
            progress_callback(
                f"Manual: copying {len(resolved)} files…",
                pct_start + (pct_end - pct_start) * 0.3)
        try:
            from data.excel_reader import batch_copy_to_local
            path_map = batch_copy_to_local(list(resolved.values()))
            t(f"  Batch copy complete: {len(path_map)} files")
        except Exception as e:
            t(f"  Batch copy unavailable ({e}), reading from network")
            path_map = {str(p): str(p) for p in resolved.values()}

        local_paths = {}
        for tid, net_path in resolved.items():
            local_paths[tid] = path_map.get(str(net_path), str(net_path))

        # Read detail files (existing function)
        total = len(local_paths)
        for i, (tid, local) in enumerate(local_paths.items()):
            if progress_callback and i % 5 == 0:
                pct = pct_start + (pct_end - pct_start) * (
                    0.4 + 0.6 * i / max(total, 1))
                progress_callback(
                    f"Manual: reading {tid} ({i+1}/{total})", int(pct))
            try:
                df = read_detail_file(local)
                if df is not None and not df.empty:
                    self.data_source.cache.store_dataframe(
                        f"detail_{tid}", df, source_path=local)
                    results.append((tid, df))
                    t(f"  [READ] {tid}: {len(df)} rows")
                else:
                    t(f"  [READ] {tid}: EMPTY")
            except Exception as e:
                t(f"  [READ] {tid}: FAILED — {e}")
                _log(f"Manual: read failed {tid}: {e}")

        return results

    # ── AFFINITY SCALING (uses existing scale_test_to_baseline) ──

    def _scale_tests(self, detail_list, test_rows, trim_col,
                     is_vertical, target_trim, target_speed,
                     is_horizontal, head_col, power_col):
        """
        Scale tests — logging matches ComparisonEngine._load_and_scale_test.
        """
        t = self._t
        row_lookup = {}
        if not test_rows.empty and "test_id" in test_rows.columns:
            for _, r in test_rows.iterrows():
                row_lookup[str(r.get("test_id", "")).strip()] = r

        try:
            target_trim = float(target_trim) if target_trim else None
        except (ValueError, TypeError):
            target_trim = None
        try:
            target_speed = float(target_speed) if target_speed else None
        except (ValueError, TypeError):
            target_speed = None

        results = []
        for tid, df in detail_list:
            row = row_lookup.get(tid)

            # Extract trim info using existing auto-mode function
            test_trim = None
            num_stages = 1
            filing_info = ""
            if row is not None:
                trim_val, nstg, filing, *_ = \
                    self._comp_engine._extract_trim_diameter(
                        row, trim_col, is_vertical=is_vertical)
                test_trim = trim_val
                num_stages = nstg if nstg else 1
                filing_info = filing or ""

            test_speed = self._get_num(row, "rpm")

            t(f"\n  ── [LOAD+SCALE] {tid} ──")
            t(f"    trim={test_trim}, stages={num_stages}, "
              f"rpm={test_speed}, filing='{filing_info}'")

            # ── Data extraction (auto-mode valid_mask pattern) ──
            if "flow" not in df.columns:
                t(f"    ✗ no 'flow' column")
                continue

            flow_series = pd.to_numeric(df["flow"], errors="coerce")
            valid_mask = flow_series.notna() & (flow_series >= 0)
            flow = flow_series[valid_mask].values.astype(float)

            if len(flow) < 2:
                t(f"    ✗ only {len(flow)} valid flow points")
                continue

            t(f"    flow: {len(flow)} pts")
            t(f"      values: {[f'{v:.2f}' for v in flow]}")

            # Head — same mask
            head = None
            if head_col in df.columns:
                h = pd.to_numeric(df[head_col], errors="coerce")
                head = h[valid_mask].values.astype(float)
                t(f"    head ('{head_col}'): {len(head)} pts")
                t(f"      values: {[f'{v:.2f}' for v in head]}")
            else:
                t(f"    ✗ head column '{head_col}' not in columns")

            # Power — same mask, with dyno↔motor fallback
            power = None
            actual_power_col = power_col
            if power_col in df.columns:
                p = pd.to_numeric(df[power_col], errors="coerce")
                p_vals = p[valid_mask].values.astype(float)
                if np.any(p_vals > 0):
                    power = p_vals
                    t(f"    power ('{power_col}'): {len(power)} pts")
                    t(f"      values: {[f'{v:.4f}' for v in power]}")
                else:
                    t(f"    power ('{power_col}'): all zero/negative "
                      f"— trying fallback")

            if power is None:
                fallback_col = None
                if "dyno" in power_col:
                    fallback_col = power_col.replace("dyno", "motor")
                elif "motor" in power_col:
                    fallback_col = power_col.replace("motor", "dyno")

                if fallback_col and fallback_col in df.columns:
                    p = pd.to_numeric(df[fallback_col], errors="coerce")
                    p_vals = p[valid_mask].values.astype(float)
                    if np.any(p_vals > 0):
                        power = p_vals
                        actual_power_col = fallback_col
                        t(f"    power FALLBACK ('{fallback_col}'): "
                          f"{len(power)} pts")
                        t(f"      values: "
                          f"{[f'{v:.4f}' for v in power]}")
                    else:
                        t(f"    ✗ fallback power '{fallback_col}' "
                          f"also zero/negative")
                elif power_col not in df.columns:
                    t(f"    ✗ power column '{power_col}' "
                      f"not in columns")

            # Efficiency — derived from actual power column
            eff_col = actual_power_col.replace("power_", "eff_")
            efficiency = None
            if eff_col in df.columns:
                e = pd.to_numeric(df[eff_col], errors="coerce")
                efficiency = e[valid_mask].values.astype(float)
                t(f"    efficiency ('{eff_col}'): "
                  f"{len(efficiency)} pts")
            elif "eff_overall" in df.columns:
                e = pd.to_numeric(
                    df["eff_overall"], errors="coerce")
                efficiency = e[valid_mask].values.astype(float)
                t(f"    efficiency ('eff_overall' fallback): "
                  f"{len(efficiency)} pts")
            else:
                t(f"    ✗ efficiency column '{eff_col}' "
                  f"not in columns")

            # ── Stage normalization ───────────────────────────
            if num_stages > 1:
                t(f"    [STAGE NORM] {num_stages}-stage → "
                  f"dividing head & power by {num_stages}")
                if head is not None:
                    t(f"      head BEFORE: "
                      f"{[f'{v:.2f}' for v in head]}")
                    head = head / num_stages
                    t(f"      head AFTER:  "
                      f"{[f'{v:.2f}' for v in head]}")
                if power is not None:
                    t(f"      power BEFORE: "
                      f"{[f'{v:.4f}' for v in power]}")
                    power = power / num_stages
                    t(f"      power AFTER:  "
                      f"{[f'{v:.4f}' for v in power]}")
            else:
                t(f"    [STAGE NORM] single-stage — no division")

            # ── Affinity scaling ──────────────────────────────
            use_trim = (test_trim if test_trim and test_trim > 0
                        else target_trim)
            use_speed = (test_speed if test_speed and test_speed > 0
                         else target_speed)
            bl_dia = (target_trim if target_trim and target_trim > 0
                      else None)
            bl_spd = (target_speed if target_speed and target_speed > 0
                      else None)

            sf, sh, sp = flow, head, power

            if bl_dia and use_trim and bl_spd and use_speed:
                t(f"    [AFFINITY]")
                t(f"      test_diameter:     {use_trim:.4f}")
                t(f"      baseline_diameter: {bl_dia:.4f}")
                t(f"      dia_ratio:         "
                  f"{bl_dia:.4f}/{use_trim:.4f} = "
                  f"{bl_dia / use_trim:.6f}")
                t(f"      test_rpm:          {use_speed}")
                t(f"      baseline_rpm:      {bl_spd}")
                if use_speed > 0 and bl_spd > 0:
                    t(f"      speed_ratio:       "
                      f"{bl_spd:.1f}/{use_speed:.1f} = "
                      f"{bl_spd / use_speed:.6f}")

                # Log horizontal slip factor if applicable
                if (is_horizontal
                        and abs(bl_dia / use_trim - 1.0) > 0.005):
                    raw_d = bl_dia / use_trim
                    eff_d = 1.2 * raw_d - 0.2
                    t(f"      HORIZONTAL slip factor: "
                      f"raw_d_ratio={raw_d:.6f}  "
                      f"eff_d_ratio=1.2×{raw_d:.4f}−0.2="
                      f"{eff_d:.6f}")

                try:
                    sc = scale_test_to_baseline(
                        test_flow=flow,
                        test_head=head,
                        test_power=power,
                        test_diameter=use_trim,
                        baseline_diameter=bl_dia,
                        test_speed=use_speed,
                        baseline_speed=bl_spd,
                        is_horizontal=is_horizontal,
                    )
                    sf, sh, sp = sc.flow, sc.head, sc.power

                    t(f"      scaled_flow:  "
                      f"{[f'{v:.2f}' for v in sc.flow]}")
                    if sc.head is not None:
                        t(f"      scaled_head:  "
                          f"{[f'{v:.2f}' for v in sc.head]}")
                    if sc.power is not None:
                        t(f"      scaled_power: "
                          f"{[f'{v:.4f}' for v in sc.power]}")

                    w = estimate_deviation_warning(
                        sc.diameter_ratio, sc.speed_ratio)
                    if w:
                        t(f"      ⚠ {w}")

                except Exception as e:
                    t(f"    [AFFINITY FAILED] {e}")
                    _log(f"Manual: scaling failed for {tid}: {e}")
                    sf, sh, sp = flow, head, power
            else:
                reason = []
                if not bl_dia:
                    reason.append(f"baseline_trim=None")
                if not use_trim:
                    reason.append(f"test_trim=None")
                if not bl_spd:
                    reason.append(f"baseline_speed=None")
                if not use_speed:
                    reason.append(f"test_speed=None")
                t(f"    [NO AFFINITY] {', '.join(reason)}")

            # ── Final logged output ───────────────────────────
            t(f"    [FINAL] plotted_flow:  "
              f"[{sf[0]:.2f} → {sf[-1]:.2f}]")
            if sh is not None:
                t(f"    [FINAL] plotted_head:  "
                  f"[{sh[0]:.2f} → {sh[-1]:.2f}]")
            if sp is not None:
                t(f"    [FINAL] plotted_power: "
                  f"[{sp[0]:.4f} → {sp[-1]:.4f}]")
            if efficiency is not None:
                t(f"    [FINAL] plotted_eff:   "
                  f"[{efficiency[0]:.2f} → {efficiency[-1]:.2f}]")

            dr = (bl_dia / use_trim) if (bl_dia and use_trim) else 1.0
            nr = (bl_spd / use_speed) if (bl_spd and use_speed) else 1.0
            w = estimate_deviation_warning(dr, nr) or ""

            results.append({
                "test_id": tid,
                "raw_flow": flow, "raw_head": head,
                "raw_power": power, "raw_eff": efficiency,
                "scaled_flow": sf, "scaled_head": sh,
                "scaled_power": sp, "scaled_eff": efficiency,
                "affinity_warning": w,
            })
        return results

    # ── RESULT ENTRIES ───────────────────────────────────────

    def _build_entries(self, scaled, test_rows, index_name,
                       trim_col, is_vertical):
        rl = {}
        if not test_rows.empty and "test_id" in test_rows.columns:
            for _, r in test_rows.iterrows():
                rl[str(r.get("test_id", "")).strip()] = r
        entries = []
        for s in scaled:
            tid = s["test_id"]
            row = rl.get(tid)
            m = self._meta(tid, row, trim_col, is_vertical)
            entries.append({
                "test_id": tid, "source_index": index_name,
                "test_date": m.get("test_date", ""),
                "trim_diameter": m.get("trim_diameter"),
                "rpm": m.get("rpm"),
                "pass_fail": m.get("pass_fail", ""),
                "num_stages": m.get("num_stages", 1),
                "filing_info": m.get("filing_info", ""),
                "upper_diameter": None, "lower_diameter": None,
                "is_polished": False,
                "raw_flow": _al(s["raw_flow"]),
                "raw_head": _al(s["raw_head"]),
                "raw_power": _al(s["raw_power"]),
                "raw_efficiency": _al(s.get("raw_eff")),
                "scaled_flow": _al(s["scaled_flow"]),
                "scaled_head": _al(s["scaled_head"]),
                "scaled_power": _al(s["scaled_power"]),
                "scaled_efficiency": _al(s.get("scaled_eff")),
                "affinity_warning": s.get("affinity_warning", ""),
                "has_data": (s["scaled_flow"] is not None
                             and len(s["scaled_flow"]) > 0),
            })
        return entries

    # ── HELPERS ──────────────────────────────────────────────

    def _load_index(self, pt):
        return self.data_source.get_index_data(self.get_index_name(pt))

    def _get_trim_column(self, pt):
        return ("material_2" if pt in ("inline", "horizontal", "end_suction")
                else "impeller_trim")

    def _filter_dates(self, df, ds=None, de=None):
        if not ds and not de:
            return df
        if "test_date" not in df.columns:
            return df
        dates = pd.to_datetime(df["test_date"], errors="coerce")
        if ds:
            try:
                df = df[dates >= pd.to_datetime(ds)]
                dates = pd.to_datetime(df["test_date"], errors="coerce")
            except Exception:
                pass
        if de:
            try:
                df = df[dates <= pd.to_datetime(de)]
            except Exception:
                pass
        return df

    def _find_test_rows(self, pt, model, ds, de, idx_df):
        df = idx_df.copy() if not idx_df.empty else pd.DataFrame()
        if df.empty or not model:
            return pd.DataFrame()
        df = self._filter_dates(df, ds, de)
        if pt == "vertical" and "bowl_size" in df.columns:
            m = re.match(r'^(\d+)([A-Za-z]+.*)$', model.strip())
            if m:
                bowl = int(m.group(1))
                letters = m.group(2).strip().upper()
                bv = pd.to_numeric(df["bowl_size"], errors="coerce")
                mv = (df["pump_model"].fillna("")
                      .astype(str).str.strip().str.upper())
                df = df[(bv == bowl) & (mv == letters)]
            else:
                df = df[df["pump_model"].astype(str).str.strip()
                        .str.upper() == model.strip().upper()]
        elif "pump_model" in df.columns:
            df = df[df["pump_model"].astype(str).str.strip()
                    .str.upper() == model.strip().upper()]

        # Bad variable names, now filters to all available tests in manual mode
        pre_first = len(df)
        df = self._comp_engine._filter_test_iterations(df, 3)
        self._t(f"  First-test filter: {len(df)} rows "
                f"(from {pre_first})")
        return df

    def _get_num(self, row, col):
        if row is None or col is None:
            return None
        v = row.get(col)
        if pd.isna(v):
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            m = re.search(r'[\d.]+', str(v))
            return float(m.group()) if m else None

    def _meta(self, tid, row, trim_col, is_vert):
        m = {"test_date": "", "trim_diameter": None, "rpm": None,
             "pass_fail": "", "num_stages": 1, "filing_info": ""}
        if row is None:
            return m
        m["test_date"] = str(row.get("test_date", ""))[:10]
        m["pass_fail"] = str(row.get("pass_fail", ""))
        m["rpm"] = float(row.get("rpm", 0) or 0)

        # Use existing _extract_trim_diameter for full VT parsing
        trim_val, num_stages, filing, *_ = \
            self._comp_engine._extract_trim_diameter(
                row, trim_col, is_vertical=is_vert)
        m["trim_diameter"] = trim_val
        m["num_stages"] = num_stages
        m["filing_info"] = filing
        return m


def _al(arr):
    if arr is None:
        return None
    try:
        a = np.asarray(arr, dtype=float)
        return a.tolist() if len(a) > 0 else None
    except Exception:
        return None
