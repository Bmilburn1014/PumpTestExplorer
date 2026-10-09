# data/psd_exporter.py
#
# PSD (Pump Selection Data) file writer.
#
# Takes the current comparison results, fit settings, and shape settings,
# recomputes head/power/efficiency best-fit curves for each visible group,
# then writes them into a PSD-format .xlsx workbook based on the
# PSD_Format.xlsx template.
#
# PSD column layout per trim (21-col block starting at col D, 0-indexed col 3):
#   [0]  Head Flow     [1]  Head       [2-6]  Flags (FALSE)
#   [7]  Power Flow    [8]  Power      [9-13] Flags (FALSE)
#   [14] NPSH Flow     [15] NPSH       [16-20] Flags (FALSE)
#
# Data rows: 10-39 (30 points, 1-indexed)

import numpy as np
from pathlib import Path
from copy import copy

# Conversion constants
FEET_TO_PSI = 1 / 2.31

# ── PSD template geometry (1-indexed rows/cols matching spec) ────────
BLOCK_WIDTH = 21
BLOCK_START_COL = 4          # Column D = 1-based col 4

# Rows (1-based)
UNITS_ROWS = (1, 4)          # Rows 1-4 carry unit labels
PSD_META_ROW = 7             # Per-trim metadata values
PSD_HEADER_ROW = 8           # Column headers
PSD_UNITS_ROW = 9            # Units row
DATA_START_ROW = 10
DATA_END_ROW = 39            # 30 data points
NUM_EXPORT_POINTS = 30

# Header sheet rows (1-based)
HDR_DATA_START_ROW = 11      # "Curve Header Data" — one row per trim

# Metadata label offsets within a block (relative to block_start, row 6)
META_LABELS = {
    1: "Head start flow",
    4: "Spline for head",
    7: "Efficiency start flow",
    8: "Power start flow",
    9: "BEP flow",
    10: "BEP efficiency",
    11: "Spline for Eta/Pwr",
    14: "NPSH start flow",
    15: "Shutoff power",
}

# Efficiency constant:  η = (Q × H) / (3960 × P) × 100
EFF_CONST = 3960.0


# =====================================================================
# PUBLIC API
# =====================================================================

def export_psd(
    template_path: str,
    output_path: str,
    px_data: dict,
    comp_data: dict,
    fit_settings: dict,
    shape_settings: dict,
    test_visibility: dict,
    group_visibility: dict,
    added_trims: list | None = None,
):
    """
    Build a PSD .xlsx file from the current analysis state.

    Parameters
    ----------
    template_path : str
        Path to blank PSD_Format.xlsx template.
    output_path : str
        Destination .xlsx path to write.
    px_data : dict
        Serialised PX data from the px-data Store.
    comp_data : dict
        Serialised comparison results from the comparison-results Store.
    fit_settings : dict
        Per-group fit settings from the fit-settings Store.
    shape_settings : dict
        Per-group shape controls from the shape-settings Store.
    test_visibility : dict
        Per-test visibility from the test-visibility Store.
    group_visibility : dict
        Per-group visibility from the group-visibility Store.
    added_trims : list or None
        Virtual trim definitions from the added-trims Store.

    Returns
    -------
    int
        Number of trims written.
    """
    import openpyxl

    wb = openpyxl.load_workbook(template_path)

    # Identify the curve data sheet (third sheet, named after curve number)
    skip = {"Curve Header (IEQ use only)", "Curve Header Data"}
    curve_ws = None
    for name in wb.sheetnames:
        if name not in skip:
            curve_ws = wb[name]
            break
    if curve_ws is None:
        curve_ws = wb.create_sheet("CurveData")

    hdr_ws = wb["Curve Header Data"] if "Curve Header Data" in wb.sheetnames else None

    # -- Rename the curve data sheet per pump type --------------------
    # Horizontal / inline:  "Model - RPM"
    # Vertical:             "Model - ImpellerPartNumber"
    model = px_data.get("model", "") if px_data else ""
    speed = px_data.get("rated_speed", 0) if px_data else 0

    try:
        from data.pump_classifier import classify_pump
        classification = classify_pump(model)
        if classification.pump_type in ("inline",):
            sheet_label = f"{model} - {int(speed)}"
        else:
            # Vertical — use impeller part number from classifier
            part = classification.impeller_part
            if part and model.endswith(f"-{part}"):
                # Part number already in the curve number (e.g. "12MBHC-2624331")
                # Use as-is to avoid "12MBHC-2624331 - 2624331"
                sheet_label = model
            elif part:
                sheet_label = f"{model} - {part}"
            else:
                sheet_label = f"{model} - {int(speed)}"
    except Exception:
        sheet_label = f"{model} - {int(speed)}" if model else "CurveData"

    # Excel sheet names limited to 31 chars, no special chars
    safe_label = sheet_label[:31].replace("/", "-").replace("\\", "-")
    curve_ws.title = safe_label

    trim_index = 0
    groups = comp_data.get("groups", []) if comp_data else []

    for gi, grp in enumerate(groups):
        if not group_visibility.get(str(gi), True):
            continue

        nom_dia = grp.get("nominal_diameter")
        try:
            dia_val = float(nom_dia)
        except (ValueError, TypeError):
            continue

        # Recompute head, power, efficiency fit curves
        head_xy = _compute_group_fit(
            "head", grp, gi, fit_settings, shape_settings,
            test_visibility, px_data,
        )
        power_xy = _compute_group_fit(
            "power", grp, gi, fit_settings, shape_settings,
            test_visibility, px_data,
        )

        if head_xy is None or power_xy is None:
            continue

        # Sample 30 evenly-spaced points from 0 to max flow
        max_flow = max(head_xy[0].max(), power_xy[0].max())
        export_flow = np.linspace(0, max_flow, NUM_EXPORT_POINTS)

        head_vals = np.interp(export_flow, head_xy[0], head_xy[1])
        power_vals = np.interp(export_flow, power_xy[0], power_xy[1])

        # Derive efficiency: η = (Q × H) / (3960 × P) × 100
        with np.errstate(divide='ignore', invalid='ignore'):
            eff_vals = np.where(
                power_vals > 0,
                (export_flow * head_vals) / (EFF_CONST * power_vals) * 100,
                0,
            )
        eff_vals = np.clip(eff_vals, 0, 100)

        # Find BEP
        bep_idx = np.argmax(eff_vals)
        bep_flow = float(export_flow[bep_idx])
        bep_eff = float(eff_vals[bep_idx])

        # Shutoff power (power at flow = 0)
        shutoff_pwr = float(power_vals[0]) if len(power_vals) > 0 else 0

        # Write to curve sheet
        _write_trim_block(
            curve_ws, trim_index, dia_val,
            export_flow, head_vals, power_vals,
            bep_flow, bep_eff, shutoff_pwr,
        )

        # Write header row
        if hdr_ws:
            _write_header_row(hdr_ws, trim_index, px_data, dia_val)

        trim_index += 1

    # ── Virtual trims ────────────────────────────────────────────
    if added_trims and px_data:
        from layout.main_layout import VIRTUAL_INDEX_OFFSET
        for vi, vt_info in enumerate(added_trims):
            fit_idx = VIRTUAL_INDEX_OFFSET + vi
            if not group_visibility.get(str(fit_idx), True):
                continue

            v_dia = float(vt_info["diameter"])

            head_xy = _compute_virtual_fit(
                "head", vt_info, vi, comp_data, px_data,
                fit_settings, test_visibility, group_visibility,
            )
            power_xy = _compute_virtual_fit(
                "power", vt_info, vi, comp_data, px_data,
                fit_settings, test_visibility, group_visibility,
            )

            if head_xy is None or power_xy is None:
                continue

            max_flow = max(head_xy[0].max(), power_xy[0].max())
            export_flow = np.linspace(0, max_flow, NUM_EXPORT_POINTS)
            head_vals = np.interp(export_flow, head_xy[0], head_xy[1])
            power_vals = np.interp(export_flow, power_xy[0], power_xy[1])

            with np.errstate(divide='ignore', invalid='ignore'):
                eff_vals = np.where(
                    power_vals > 0,
                    (export_flow * head_vals) / (EFF_CONST * power_vals) * 100,
                    0,
                )
            eff_vals = np.clip(eff_vals, 0, 100)

            bep_idx = np.argmax(eff_vals)
            bep_flow = float(export_flow[bep_idx])
            bep_eff = float(eff_vals[bep_idx])
            shutoff_pwr = float(power_vals[0]) if len(power_vals) > 0 else 0

            _write_trim_block(
                curve_ws, trim_index, v_dia,
                export_flow, head_vals, power_vals,
                bep_flow, bep_eff, shutoff_pwr,
            )

            if hdr_ws:
                _write_header_row(hdr_ws, trim_index, px_data, v_dia)

            trim_index += 1

    wb.save(output_path)
    return trim_index


# =====================================================================
# COMPUTE FIT FOR A REAL GROUP  (mirrors chart_callbacks logic)
# =====================================================================

def _compute_group_fit(chart_type, grp, gi, fit_settings,
                       shape_settings, test_vis, px_data):
    """
    Recompute the best-fit line for *chart_type* ('head' or 'power')
    using the same logic as chart_callbacks._build_chart.
    Returns (x_fit, y_fit) arrays or None.
    """
    chart_fit_settings = (fit_settings or {}).get(str(chart_type), {})
    chart_shape_settings = (shape_settings or {}).get(str(chart_type), {})
    
    fs = chart_fit_settings.get(str(gi), {})
    poly_order = int(fs.get("poly_order", 3))
    offset_pct = float(fs.get("offset_pct", 0))
    min_pct, max_pct = fs.get("bep_range_pct", [0,100])

    ss = chart_shape_settings.get(str(gi), {})
    droop_on = ss.get("droop_enabled", False)
    droop_pct = float(ss.get("droop_pct", 3))
    carryout_on = ss.get("carryout_enabled", False)
    carryout_pct = float(ss.get("carryout_pct", 3))
    spline_on = ss.get("spline_on", False)
    smoothing = float(ss.get("smoothing", 0.3))
    knots = ss.get("knots", [])

    nom_dia = grp.get("nominal_diameter")

    all_flow, all_y = _gather_test_data(
        grp, gi, chart_type, test_vis,px_data)

    if len(all_flow) < 3:
        return None

    all_flow_arr = np.array(all_flow)
    all_y_arr = np.array(all_y)
    data_min = all_flow_arr.min()
    data_max = all_flow_arr.max()

    # Baseline max flow
    baseline_max = data_max
    if px_data:
        try:
            nd = float(nom_dia)
            for pt in px_data.get("trims", []):
                if abs(pt["diameter"] - nd) < 0.05:
                    hf = pt.get("head_flow", [])
                    if hf:
                        baseline_max = max(baseline_max, max(hf))
                    break
        except (ValueError, TypeError):
            pass

    full_max = max(data_max, baseline_max)
    min_fit = data_min + (full_max - data_min) * (min_pct / 100)
    max_fit = data_min + (full_max - data_min) * (max_pct / 100)

    fm = (
        (all_flow_arr >= min_fit) &
        (all_flow_arr <= max_fit)
    )

    fit_f = all_flow_arr[fm]
    fit_y = all_y_arr[fm]

    if len(fit_f) < 3:
        return None

    x_fit, y_fit = _do_fit(fit_f, fit_y, poly_order, max_fit,
                           spline_on, knots, smoothing)

    if offset_pct != 0:
        y_fit = y_fit * (1 + offset_pct / 100)
    if droop_on and droop_pct != 0 and chart_type in ("head", "power"):
        fr = x_fit.max() - x_fit.min()
        y_fit = _apply_droop(x_fit, y_fit, droop_pct, x_fit.min(), fr)
    if carryout_on and carryout_pct != 0 and chart_type in ("head", "power"):
        fr = x_fit.max() - x_fit.min()
        y_fit = _apply_carryout(x_fit, y_fit, carryout_pct,
                                x_fit.max(), fr)

    return x_fit, y_fit


# =====================================================================
# COMPUTE FIT FOR A VIRTUAL GROUP
# =====================================================================

def _compute_virtual_fit(chart_type, vt_info, vi, comp_data, px_data,
                         fit_settings, test_vis, group_vis):
    """Compute the fit line for a virtual (added) trim group."""
    from layout.main_layout import VIRTUAL_INDEX_OFFSET

    v_dia = float(vt_info["diameter"])
    fit_idx = VIRTUAL_INDEX_OFFSET + vi

    fs = fit_settings.get(str(fit_idx), {})
    poly_order = int(fs.get("poly_order", 3))
    offset_pct = float(fs.get("offset_pct", 0))
    fit_range_pct = float(fs.get("bep_range_pct", 100))

    px_diameters = [t["diameter"] for t in px_data.get("trims", [])]
    if not px_diameters:
        return None

    # Gather virtual test data (same as chart_callbacks)
    all_tests = []
    for grp in comp_data.get("groups", []):
        for t in grp.get("tests", []):
            if t.get("has_data"):
                all_tests.append(t)

    virtual_tests = []
    for t in all_tests:
        td = t.get("trim_diameter")
        if td is None:
            continue
        if abs(td - v_dia) < min(abs(td - d) for d in px_diameters):
            virtual_tests.append(t)

    all_vflow, all_vy = [], []
    ti_counter = 0
    for t in virtual_tests:
        vis_key = f"{fit_idx}-{ti_counter}"
        is_visible = test_vis.get(vis_key, True)
        ti_counter += 1

        if chart_type == "head":
            fd, yd = t.get("scaled_flow"), t.get("scaled_head")
        elif chart_type == "power":
            fd, yd = t.get("scaled_flow"), t.get("scaled_power")
        else:
            continue
        if fd is None or yd is None:
            continue

        fa = np.array(fd, dtype=float)
        ya = np.array(yd, dtype=float)
        t_trim = t.get("trim_diameter", v_dia)
        rescale = v_dia / t_trim if t_trim > 0 else 1.0
        fa = fa * rescale
        if chart_type == "head":
            ya = ya * (rescale ** 2)
        elif chart_type == "power":
            ya = ya * (rescale ** 3)

        m = np.isfinite(fa) & np.isfinite(ya)
        fa, ya = fa[m], ya[m]
        if len(fa) < 1 or not is_visible:
            continue

        all_vflow.extend(fa.tolist())
        all_vy.extend(ya.tolist())

    if len(all_vflow) < 3:
        return None

    vf = np.array(all_vflow)
    vy = np.array(all_vy)
    vdata_min, vdata_max = vf.min(), vf.max()

    # Nearest PX trim for baseline max
    nearest_px_dia = min(px_diameters, key=lambda d: abs(d - v_dia))
    nearest_px_trim = None
    for pt in px_data.get("trims", []):
        if abs(pt["diameter"] - nearest_px_dia) < 0.001:
            nearest_px_trim = pt
            break

    d_ratio = v_dia / nearest_px_dia if nearest_px_dia else 1.0
    v_baseline_max = vdata_max
    if nearest_px_trim:
        hf = nearest_px_trim.get("head_flow", [])
        if hf:
            v_baseline_max = max(hf) * d_ratio

    v_full_max = max(vdata_max, v_baseline_max)

    if fit_range_pct < 100:
        v_max_fit = vdata_min + (v_full_max - vdata_min) * (fit_range_pct / 100)
        fm = vf <= v_max_fit
        vf_fit, vy_fit = vf[fm], vy[fm]
    else:
        v_max_fit = v_full_max
        vf_fit, vy_fit = vf, vy

    if len(vf_fit) < 3:
        return None

    x_fit, y_fit = _do_fit(vf_fit, vy_fit, poly_order, v_max_fit,
                           False, [], 0.3)

    if offset_pct != 0:
        y_fit = y_fit * (1 + offset_pct / 100)

    return x_fit, y_fit


# =====================================================================
# GATHER VISIBLE TEST DATA
# =====================================================================

def _gather_test_data(grp, gi, chart_type, test_vis, px_data):
    """Collect flow/y arrays from visible tests in a group."""
    all_flow, all_y = [], []
    ti_counter = 0
    for test in grp.get("tests", []):
        if not test.get("has_data"):
            continue
        vis_key = f"{gi}-{ti_counter}"
        is_visible = test_vis.get(vis_key, True)
        ti_counter += 1

        if chart_type == "head":
            flow_key, y_key = "scaled_flow", "scaled_head"
        elif chart_type == "power":
            flow_key, y_key = "scaled_flow", "scaled_power"
        else:
            continue

        flow_data = test.get(flow_key)
        y_data = test.get(y_key)
        if flow_data is None or y_data is None:
            continue

        flow_arr = np.array(flow_data, dtype=float)
        y_arr = np.array(y_data, dtype=float)
        mask = np.isfinite(flow_arr) & np.isfinite(y_arr)
        flow_arr = flow_arr[mask]
        y_arr = y_arr[mask]

        if len(flow_arr) < 1 or not is_visible:
            continue

        all_flow.extend(flow_arr.tolist())
        all_y.extend(y_arr.tolist())
    if all_flow == 0:
        all_flow, all_y  = get_group_baseline_data(px_data, grp, gi, chart_type)
    return all_flow, all_y


# =====================================================================
# FIT (poly or spline) — mirrors chart_callbacks logic
# =====================================================================

def _do_fit(fit_f, fit_y, poly_order, max_fit,
            spline_on, knots, smoothing):
    """Run poly or spline fit, returning (x_fit, y_fit)."""
    if spline_on:
        if any(isinstance(k, dict) and k.get("y") is not None for k in (knots or [])):
            x_fit, y_fit = _fit_absolute_knots(knots, smoothing)
        else:
            kf = [k["flow"] for k in knots]
            kn = [k["nudge_pct"] for k in knots]
            x_fit, y_fit = _fit_piecewise_spline(fit_f, fit_y, kf, kn, smoothing)
        trunc = x_fit <= max_fit
        if np.any(trunc):
            x_fit = x_fit[trunc]
            y_fit = y_fit[trunc]
    else:
        try:
            order = min(poly_order, len(fit_f) - 1)
            coeffs = np.polyfit(fit_f, fit_y, order)
            x_fit = np.linspace(fit_f.min(), max_fit, 200)
            y_fit = np.polyval(coeffs, x_fit)
        except Exception:
            x_fit = np.sort(fit_f)
            y_fit = fit_y[np.argsort(fit_f)]

    return x_fit, y_fit



def _fit_absolute_knots(knots, smoothing=0.3):
    """Spline through dragged knot positions. Matches chart_callbacks."""
    pts = []
    for knot in knots or []:
        try:
            pts.append((float(knot["flow"]), float(knot["y"])))
        except (KeyError, TypeError, ValueError):
            continue
    pts.sort(key=lambda item: item[0])
    xs, ys = [], []
    for flow, value in pts:
        if not (np.isfinite(flow) and np.isfinite(value)):
            continue
        if xs and abs(flow - xs[-1]) < 1e-6:
            ys[-1] = (ys[-1] + value) / 2.0
            continue
        xs.append(flow)
        ys.append(value)
    if len(xs) < 2:
        return np.array([]), np.array([])
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    x_fit = np.linspace(xs.min(), xs.max(), 200)
    if len(xs) < 4:
        return x_fit, np.interp(x_fit, xs, ys)
    smooth = max(0.0, float(smoothing or 0))
    try:
        if smooth <= 0.15:
            from scipy.interpolate import PchipInterpolator
            return x_fit, PchipInterpolator(xs, ys)(x_fit)
        from scipy.interpolate import UnivariateSpline
        variance = float(np.var(ys)) or 1.0
        s_param = smooth * len(xs) * variance * 0.5
        spline = UnivariateSpline(xs, ys, s=s_param, k=3)
        return x_fit, spline(x_fit)
    except Exception:
        return x_fit, np.interp(x_fit, xs, ys)


def _fit_piecewise_spline(flow, y, knot_flows, knot_nudges,
                          smoothing=0.3):
    """Spline fit with knot control — copied from chart_callbacks."""
    idx = np.argsort(flow)
    flow_s = flow[idx]
    y_s = y[idx]
    flow_min, flow_max = flow_s.min(), flow_s.max()
    flow_range = flow_max - flow_min

    if flow_range <= 0 or len(flow_s) < 3:
        return flow_s, y_s

    n_bins = max(12, len(flow_s) // 8)
    bin_edges = np.linspace(flow_min, flow_max, n_bins + 1)

    rep_x, rep_y, rep_w = [], [], []
    for bi in range(n_bins):
        lo, hi = bin_edges[bi], bin_edges[bi + 1]
        mask = ((flow_s >= lo) & (flow_s <= hi) if bi == n_bins - 1
                else (flow_s >= lo) & (flow_s < hi))
        seg = y_s[mask]
        seg_x = flow_s[mask]
        if len(seg) == 0:
            continue
        rep_x.append(np.median(seg_x))
        rep_y.append(np.median(seg))
        rep_w.append(np.sqrt(len(seg)))

    if len(rep_x) < 4:
        coeffs = np.polyfit(flow_s, y_s, min(3, len(flow_s) - 1))
        x_fit = np.linspace(flow_min, flow_max, 200)
        return x_fit, np.polyval(coeffs, x_fit)

    rep_x = np.array(rep_x)
    rep_y = np.array(rep_y)
    rep_w = np.array(rep_w)

    for kf, kn in zip(knot_flows, knot_nudges):
        if kf < flow_min or kf > flow_max:
            continue
        radius = max(flow_range * 0.05, 1.0)
        nearby = np.abs(flow_s - kf) <= radius
        local_y = (np.median(y_s[nearby]) if nearby.sum() > 0
                   else np.interp(kf, rep_x, rep_y))
        rep_x = np.append(rep_x, kf)
        rep_y = np.append(rep_y, local_y * (1 + kn / 100))
        rep_w = np.append(rep_w, rep_w.max() * 5)

    sort_idx = np.argsort(rep_x)
    rep_x, rep_y, rep_w = rep_x[sort_idx], rep_y[sort_idx], rep_w[sort_idx]

    unique_x, inv = np.unique(rep_x, return_inverse=True)
    if len(unique_x) < len(rep_x):
        new_y = np.zeros_like(unique_x)
        new_w = np.zeros_like(unique_x)
        for i, ui in enumerate(inv):
            new_y[ui] += rep_y[i] * rep_w[i]
            new_w[ui] += rep_w[i]
        new_y /= new_w
        rep_x, rep_y, rep_w = unique_x, new_y, new_w

    if len(rep_x) < 4:
        coeffs = np.polyfit(flow_s, y_s, min(3, len(flow_s) - 1))
        x_fit = np.linspace(flow_min, flow_max, 200)
        return x_fit, np.polyval(coeffs, x_fit)

    try:
        from scipy.interpolate import UnivariateSpline
        variance = np.var(rep_y) if np.var(rep_y) > 0 else 1.0
        s_param = smoothing * len(rep_x) * variance * 0.01
        spline = UnivariateSpline(rep_x, rep_y, w=rep_w, s=s_param, k=3)
        x_fit = np.linspace(flow_min, flow_max, 200)
        y_fit = spline(x_fit)
    except Exception:
        coeffs = np.polyfit(flow_s, y_s, min(3, len(flow_s) - 1))
        x_fit = np.linspace(flow_min, flow_max, 200)
        y_fit = np.polyval(coeffs, x_fit)

    return x_fit, y_fit


# =====================================================================
# DROOP / CARRYOUT — copied from chart_callbacks
# =====================================================================

def _apply_droop(x, y, pct, flow_min, flow_range):
    y = y.copy()
    if flow_range <= 0:
        return y
    end = flow_min + 0.25 * flow_range
    for i in range(len(x)):
        if x[i] <= end:
            t = (x[i] - flow_min) / (end - flow_min + 1e-9)
            t = max(0.0, min(1.0, t))
            y[i] *= (1 - pct / 100 * (1 - t))
    return y


def _apply_carryout(x, y, pct, flow_max, flow_range):
    y = y.copy()
    if flow_range <= 0:
        return y
    start = flow_max - 0.25 * flow_range
    for i in range(len(x)):
        if x[i] >= start:
            t = (x[i] - start) / (flow_max - start + 1e-9)
            t = max(0.0, min(1.0, t))
            y[i] *= (1 + pct / 100 * t)
    return y


# =====================================================================
# WRITE ONE TRIM BLOCK TO THE CURVE SHEET
# =====================================================================

def _write_trim_block(ws, trim_index, diameter,
                      flow, head, power,
                      bep_flow, bep_eff, shutoff_pwr):
    """
    Write head/power data for one trim into a 21-column block.

    Parameters
    ----------
    ws : openpyxl Worksheet
    trim_index : int   (0 = first trim)
    diameter : float
    flow : ndarray     (30 points)
    head : ndarray     (30 points)
    power : ndarray    (30 points)
    bep_flow : float
    bep_eff : float
    shutoff_pwr : float
    """
    block_col = BLOCK_START_COL + trim_index * BLOCK_WIDTH  # 1-based

    # Row 7 — diameter value at the block start
    ws.cell(row=7, column=block_col, value=diameter)

    # Row 7 metadata values (all at specific offsets)
    ws.cell(row=7, column=block_col + 1, value=0)          # Head start flow
    ws.cell(row=7, column=block_col + 4, value=False)       # Spline for head
    ws.cell(row=7, column=block_col + 7, value=0)           # Efficiency start flow
    ws.cell(row=7, column=block_col + 8, value=0)           # Power start flow
    ws.cell(row=7, column=block_col + 9, value=bep_flow)    # BEP flow
    ws.cell(row=7, column=block_col + 10, value=bep_eff)    # BEP efficiency
    ws.cell(row=7, column=block_col + 11, value=False)      # Spline for Eta/Pwr
    ws.cell(row=7, column=block_col + 14, value=0)          # NPSH start flow
    ws.cell(row=7, column=block_col + 15, value=shutoff_pwr)  # Shutoff power

    # Write 30 data rows
    for i in range(min(len(flow), NUM_EXPORT_POINTS)):
        row = DATA_START_ROW + i

        # Diameter in column A (only once per trim set, first data row)
        if i == 0:
            ws.cell(row=row, column=1, value=diameter)

        # Point number in column C
        ws.cell(row=row, column=3, value=i + 1)

        # Head: flow, value, + 5 FALSE flags
        ws.cell(row=row, column=block_col + 0, value=round(float(flow[i]), 2))
        ws.cell(row=row, column=block_col + 1, value=round(float(head[i]), 4))
        for flag_off in range(2, 7):
            ws.cell(row=row, column=block_col + flag_off, value=False)

        # Power: flow, value, + 5 FALSE flags
        ws.cell(row=row, column=block_col + 7, value=round(float(flow[i]), 2))
        ws.cell(row=row, column=block_col + 8, value=round(float(power[i]), 4))
        for flag_off in range(9, 14):
            ws.cell(row=row, column=block_col + flag_off, value=False)

        # NPSH: leave empty (offsets 14-20)
        # Explicitly clear just in case template has data
        for npsh_off in range(14, 21):
            ws.cell(row=row, column=block_col + npsh_off, value=None)


# =====================================================================
# WRITE ONE ROW TO "Curve Header Data"
# =====================================================================

def _write_header_row(ws, trim_index, px_data, diameter):
    """Write a single trim's metadata row in the Curve Header Data sheet."""
    row = HDR_DATA_START_ROW + trim_index

    model = px_data.get("model", "") if px_data else ""
    speed = px_data.get("rated_speed", 0) if px_data else 0

    ws.cell(row=row, column=1, value=model)          # A: curve number
    ws.cell(row=row, column=2, value="")              # B: revision
    ws.cell(row=row, column=3, value=speed)           # C: speed
    ws.cell(row=row, column=4, value=0)               # D: poles
    ws.cell(row=row, column=5, value=60)              # E: Hz
    ws.cell(row=row, column=6, value=diameter)         # F: diameter

    # Spec fields: N=1 (eye count), O=5 (nss increment),
    # P="Round up", Q=False, S-W=zeros
    ws.cell(row=row, column=14, value=1)              # N: eye count
    ws.cell(row=row, column=15, value=5)              # O: nss increment
    ws.cell(row=row, column=16, value="Round up")     # P
    ws.cell(row=row, column=17, value=False)          # Q
    for c in range(19, 24):                            # S-W: zeros
        ws.cell(row=row, column=c, value=0)

import math
import numpy as np


def get_group_baseline_data(
    px_data,
    group,
    group_id,
    chart_type,
    *,
    diameter_tolerance=1e-6,
):
    """
    Return baseline data for one trim group.

    Returns
    -------
    trim_dict, flow, vals
        Returns (None, None, None) when no matching trim or usable
        baseline data is found.
    """
    if not px_data or not group:
        return None, None, None

    trims = px_data.get("trims", [])

    if not trims:
        return None, None, None

    # Prefer an explicit diameter stored on the group.
    group_diameter = group.get("nominal_diameter")

    if group_diameter is None:
        group_diameter = group.get("diameter")

    if group_diameter is None:
        print(
            "Baseline lookup failed: group has no diameter",
            {
                "group_id": group_id,
                "chart_type": chart_type,
            },
            flush=True,
        )
        return None, None, None

    try:
        group_diameter = float(group_diameter)
    except (TypeError, ValueError):
        print(
            "Baseline lookup failed: invalid group diameter",
            {
                "group_id": group_id,
                "diameter": group_diameter,
            },
            flush=True,
        )
        return None, None, None

    matched_trim = None

    for trim_dict in trims:
        trim_diameter = trim_dict.get("diameter")

        if trim_diameter is None:
            continue

        try:
            trim_diameter = float(trim_diameter)
        except (TypeError, ValueError):
            continue

        if math.isclose(
            trim_diameter,
            group_diameter,
            rel_tol=0.0,
            abs_tol=diameter_tolerance,
        ):
            matched_trim = trim_dict
            break

    if matched_trim is None:
        print(
            "No matching baseline trim",
            {
                "group_id": group_id,
                "group_diameter": group_diameter,
                "available_diameters": [
                    trim.get("diameter")
                    for trim in trims
                ],
            },
            flush=True,
        )
        return None, None, None

    if chart_type == "efficiency":
        flow, vals = _compute_baseline_efficiency(
            matched_trim
        )
    else:
        flow, vals = _get_baseline_data(
            matched_trim,
            chart_type,
        )

    if flow is None or vals is None:
        return matched_trim, None, None

    flow = np.asarray(flow, dtype=float)
    vals = np.asarray(vals, dtype=float)

    if flow.shape != vals.shape:
        raise ValueError(
            "Baseline flow and value arrays have different shapes: "
            f"{flow.shape} and {vals.shape}. "
            f"Chart={chart_type!r}, group={group_id!r}"
        )

    valid = np.isfinite(flow) & np.isfinite(vals)
    flow = flow[valid]
    vals = vals[valid]

    if len(flow) == 0:
        return matched_trim, None, None

    # Keep flow and vals aligned while sorting by flow.
    order = np.argsort(flow)
    flow = flow[order]
    vals = vals[order]

    return flow, vals

def _get_baseline_data(trim_dict, chart_type):
    if chart_type == "head":
        flow = trim_dict.get("head_flow", [])
        vals = trim_dict.get("head", [])
    elif chart_type == "power":
        flow = trim_dict.get("power_flow", [])
        vals = trim_dict.get("power", [])
    else:
        return None, None
    if not flow or not vals:
        return None, None
    flow = np.array(flow, dtype=float)
    vals = np.array(vals, dtype=float)
    mask = np.isfinite(flow) & np.isfinite(vals) & (flow >= 0)
    flow, vals = flow[mask], vals[mask]
    if len(flow) < 2:
        return None, None
    idx = np.argsort(flow)
    flow, vals = flow[idx], vals[idx]
    unique_flow, inverse = np.unique(flow, return_inverse=True)
    if len(unique_flow) < len(flow):
        uv = np.zeros_like(unique_flow)
        uc = np.zeros_like(unique_flow)
        for i, ui in enumerate(inverse):
            uv[ui] += vals[i]
            uc[ui] += 1
        flow, vals = unique_flow, uv / uc
    if len(flow) < 2:
        return None, None
    return flow, vals

def _compute_baseline_efficiency(trim_dict):
    head_flow, head_vals = _get_baseline_data(trim_dict, "head")
    power_flow, power_vals = _get_baseline_data(trim_dict, "power")

    if head_flow is None or power_flow is None:
        return None, None

    flow_min = max(head_flow.min(), power_flow.min())
    flow_max = min(head_flow.max(), power_flow.max())
    if flow_min >= flow_max:
        return None, None

    common_flow = np.linspace(flow_min, flow_max, 50)
    head_interp = np.interp(common_flow, head_flow, head_vals)
    power_interp = np.interp(common_flow, power_flow, power_vals)

    with np.errstate(divide='ignore', invalid='ignore'):
        efficiency = np.where(
            power_interp > 0,
            (common_flow * head_interp) / (3960.0 * power_interp) * 100,
            0)

    valid = (efficiency > 0) & (efficiency <= 100) & (common_flow > 0)
    if valid.sum() < 2:
        return None, None
    return common_flow[valid], efficiency[valid]