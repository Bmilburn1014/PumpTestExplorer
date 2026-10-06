# callbacks/chart_callbacks.py
#
# Chart rendering for the PX comparison tool.
#
# Traces per chart:
#   PX baseline curves — smooth spline lines (black)
#   Acceptance band    — shaded ±N% around each baseline (head/power)
#   Test data points   — markers only, colored by PX trim group
#   Line of best fit   — polyfit OR piecewise spline through visible
#                         test points per group, with shape controls
#
# Efficiency:
#   PX baseline η = (Q × H) / (3960 × P) × 100
#   Test efficiency from detail file columns (dimensionless, no scaling)

import pandas as pd
import numpy as np
from dash import callback, Input, Output, State, ALL, no_update, ctx, Patch
import plotly.graph_objects as go
from dash.exceptions import PreventUpdate
from layout.main_layout import GROUP_COLORS, MAX_KNOTS
from datetime import datetime, timedelta
import re

# -- Constants ---------------------------------------------------------
FEET_TO_PSI = 1 / 2.31
BASELINE_COLOR = "#333333"
BAND_FILL_COLOR = "rgba(200,200,200,0.2)"
BAND_LINE_COLOR = "rgba(150,150,150,0.4)"
CHART_BG = "#fdfdfd"
GRID_COLOR = "#e8e8e8"
SMOOTH_POINTS = 200
VIRTUAL_COLORS = [
    "#e91e63", "#00bcd4", "#ff9800", "#4caf50", "#9c27b0",
]
VIRTUAL_INDEX_OFFSET = 100


# =====================================================================
# TAB VISIBILITY TOGGLE
# =====================================================================

@callback(
    Output("single-chart-wrap", "className"),
    Output("combined-chart-wrap", "className"),
    Input("chart-tabs", "value"),
)
def toggle_chart_visibility(tab):
    if tab == "combined":
        return "chart-area hidden", "chart-area"
    return "chart-area", "chart-area hidden"


# =====================================================================
# COLLECT TEST VISIBILITY
# =====================================================================

@callback(
    Output("test-visibility", "data"),
    Input({"type": "test-vis", "index": ALL},"value",),
    State({"type": "test-vis", "index": ALL},"id",),
)
def collect_test_visibility(vis_values, vis_ids):
    triggered_id = ctx.triggered_id

    if not isinstance(triggered_id, dict):
        raise PreventUpdate

    triggered_index = str(triggered_id.get("index"))

    triggered_value = None
    value_found = False

    for component_id, value in zip(
        vis_ids or [],
        vis_values or [],
    ):
        if str(component_id.get("index")) == triggered_index:
            triggered_value = value
            value_found = True
            break

    if not value_found:
        raise PreventUpdate

    patch = Patch()
    patch[triggered_index] = bool(triggered_value)

    return patch


# =====================================================================
# COLLECT GROUP VISIBILITY
# =====================================================================

@callback(
    Output("group-visibility", "data"),
    Input({"type": "group-vis", "index": ALL},"value",),
    State({"type": "group-vis", "index": ALL},"id",),
)
def collect_group_visibility(vis_values, vis_ids):
    triggered_id = ctx.triggered_id

    if not isinstance(triggered_id, dict):
        raise PreventUpdate

    triggered_index = str(triggered_id.get("index"))

    triggered_value = None
    value_found = False

    for component_id, value in zip(
        vis_ids or [],
        vis_values or [],
    ):
        if str(component_id.get("index")) == triggered_index:
            triggered_value = value
            value_found = True
            break

    if not value_found:
        raise PreventUpdate

    patch = Patch()
    patch[triggered_index] = bool(triggered_value)

    return patch



# =====================================================================
# COLLECT FIT SETTINGS
# =====================================================================

@callback(
    Output("fit-settings", "data"),

    Input({"type": "fit-poly","index": ALL,"chart": ALL,},"value",),
    Input({"type": "fit-offset","index": ALL,"chart": ALL,},"value",),
    Input({"type": "bep-range","index": ALL,"chart": ALL,},"value",),

    State("fit-settings", "data"),
    prevent_intital_call=True,
)
def collect_fit_settings(
    poly_values, offset_values,
    range_values, store
):

    if not ctx.triggered_id or not isinstance(ctx.triggered_id, dict):
        raise PreventUpdate
    triggered = ctx.triggered_id
    control = triggered["type"]
    group_key = str(triggered["index"]).split("-")[0]
    chart = str(triggered.get("chart", store.get("active", "head")))
    new_value = ctx.triggered[0]["value"]

    store = store or {"head": {}, "power": {}, "active": "head"}
    store.setdefault(chart, {})
    store[chart].setdefault(group_key, {})

    fs = store[chart][group_key]

    if control == "fit-poly":
        fs["poly_order"] = int(new_value or 3)
    elif control == "fit-offset":
        fs["offset_pct"] = float(new_value or 0)
    elif control == "bep-range":
        if isinstance(new_value, (list, tuple)) and len(new_value) == 2:
            fs["bep_range_pct"] = [float(new_value[0] or 0), float(new_value[1] or 100)]
        else:
            fs["bep_range_pct"] = [0, 100]
            
    return store

#======================================================================
# Sync Active with Chart Tab
#======================================================================
@callback(
        Output("shape-settings", "data", allow_duplicate=True),
        Output("fit-settings", "data", allow_duplicate=True),
        Output("test-visibility", "data", allow_duplicate=True),
        Output("group-visibility", "data", allow_duplicate=True),
        Input("chart-tabs", "value"),
        State("shape-settings", "data"),
        State("fit-settings", "data"),
        State("test-visibility", "data"),
        State("group-visibility", "data"),
        prevent_initial_call=True,
)
def set_active_shape_state(tab, shape_settings, fit_settings, test_visibility, group_visibility):
    if tab not in ("head", "power"):
        raise PreventUpdate
    ss = shape_settings or {"head": {}, "power": {}, "active": "head"}
    ss["active"] = tab

    fs = fit_settings or {"head": {}, "power": {}, "active": "head"}
    fs["active"] = tab

    tv = test_visibility or {"head": {}, "power": {}, "active": "head"}
    tv["active"] = tab

    gv = group_visibility or {"head": {}, "power": {}, "active": "head"}
    gv["active"] = tab
    return ss, fs, tv, gv

# =====================================================================
# COLLECT SHAPE SETTINGS
# =====================================================================

@callback(
    Output("shape-settings", "data"),

    Input({"type": "shape-droop-on","index": ALL,"chart": ALL,},"value",),
    Input({"type": "shape-droop-pct","index": ALL,"chart": ALL,},"value",),
    Input({"type": "shape-carryout-on","index": ALL,"chart": ALL,},"value",),
    Input({"type": "shape-carryout-pct","index": ALL,"chart": ALL,},"value",),
    Input({"type": "shape-spline-on","index": ALL,"chart": ALL,},"value",),
    Input({"type": "shape-smoothing","index": ALL,"chart": ALL,},"value",),
    Input({"type": "knot-flow","index": ALL,"chart": ALL,},"value",),
    Input({"type": "knot-nudge","index": ALL,"chart": ALL,},"value",),

    State("shape-settings", "data"),
    prevent_intial_call=True,
)
def collect_shape_settings(
    droop_on_vals, droop_pct_vals,
    carryout_on_vals, carryout_pct_vals,
    spline_on_vals, smoothing_vals,
    knot_flow_vals, knot_nudge_vals,
    store
):
    if not ctx.triggered_id or not isinstance(ctx.triggered_id, dict):
        raise PreventUpdate
    print("collect_shape_settings fired by:", ctx.triggered_id, flush=True)
    triggered = ctx.triggered_id
    control = triggered["type"]
    group_key = str(triggered["index"]).split("-")[0]
    chart = str(triggered.get("chart", store.get("active", "head")))
    new_value = ctx.triggered[0]["value"]

    store = store or {"head": {}, "power": {}, "active": "head"}
    store.setdefault(chart, {})
    store[chart].setdefault(group_key, {})

    ss = store[chart][group_key]

    if control == "shape-droop-on":
        ss["droop_enabled"] = "on" in (new_value or [])
    elif control == "shape-droop-pct":
        ss["droop_pct"] = float(new_value or 0)
    elif control == "shape-carryout-on":
        ss["carryout_enabled"] = "on" in (new_value or [])
    elif control == "shape-carryout-pct":
        ss["carryout_pct"] = float(new_value or 0)
    elif control == "shape-spline-on":
        ss["spline_on"] = "on" in (new_value or [])
    elif control == "shape-smoothing":
        ss["smoothing"] = float(new_value or 0.3)
    elif control in ("knot-flow", "knot-nudge"):
        print(f"Shape settings update: {control} for chart {chart}, group {group_key}, value: {new_value}", flush=True)
        # ss["knots"] = built_knots_for_group(
        #     chart, group_key,
            
        # )

    return store


# =====================================================================
# HELPER FUNCTION TO BUILD KNOTS FOR A GROUP
# =====================================================================

def build_knots_for_group(
    chart_key,
    group_key,
    knot_flow_ids,
    knot_flow_vals,
    knot_nudge_ids,
    knot_nudge_vals,
):
    """Collect all valid knots for one chart and group."""
    print('build_knots_for_group fired.', flush=True)
    nudge_by_index = {}

    for component_id, value in zip(
        knot_nudge_ids or [],
        knot_nudge_vals or [],
    ):
        if str(component_id.get("chart")) != chart_key:
            continue

        combined_index = str(component_id.get("index", ""))
        parts = combined_index.rsplit("-", 1)

        if len(parts) != 2:
            continue

        knot_group, _ = parts

        if knot_group != group_key:
            continue

        nudge_by_index[combined_index] = value

    knots = []

    for component_id, flow_value in zip(
        knot_flow_ids or [],
        knot_flow_vals or [],
    ):
        if str(component_id.get("chart")) != chart_key:
            continue

        combined_index = str(component_id.get("index", ""))
        parts = combined_index.rsplit("-", 1)

        if len(parts) != 2:
            continue

        knot_group, _ = parts

        if knot_group != group_key:
            continue

        if flow_value is None:
            continue

        try:
            flow = float(flow_value)
        except (TypeError, ValueError):
            continue

        if flow <= 0:
            continue

        nudge_value = nudge_by_index.get(combined_index, 0)

        try:
            nudge_pct = float(
                nudge_value if nudge_value is not None else 0
            )
        except (TypeError, ValueError):
            nudge_pct = 0.0

        knots.append(
            {
                "flow": flow,
                "nudge_pct": nudge_pct,
            }
        )

    knots.sort(key=lambda knot: knot["flow"])

    return knots

# =====================================================================
# MAIN CHART BUILDER
# =====================================================================

@callback(
    Output("main-chart", "figure"),
    Input("chart-tabs", "value"),
    Input("unit-toggle", "value"),
    Input("px-data", "data"),
    Input("comparison-results", "data"),
    Input("show-baseline", "value"),
    Input("band-pct", "value"),
    Input("fit-settings", "data"),
    Input("test-visibility", "data"),
    Input("shape-settings", "data"),
    Input("added-trims", "data"),
    Input("group-visibility", "data"),
)
def update_main_chart(tab, units, px_data, comp_data,
                      show_baseline, band_pct, fit_settings,
                      test_vis, shape_settings, added_trims,
                      group_vis):
    if tab == "combined":
        return no_update
    chart_type = tab or "head"
    return _build_chart(chart_type, units or "feet",
                        px_data, comp_data,
                        "on" in (show_baseline or []),
                        band_pct or 0,
                        fit_settings or {},
                        test_vis or {},
                        shape_settings or {},
                        added_trims or [],
                        group_vis or {})


@callback(
    Output("combined-chart", "figure"),
    Input("chart-tabs", "value"),
    Input("unit-toggle", "value"),
    Input("px-data", "data"),
    Input("comparison-results", "data"),
    Input("show-baseline", "value"),
    Input("band-pct", "value"),
    Input("fit-settings", "data"),
    Input("test-visibility", "data"),
    Input("shape-settings", "data"),
    Input("added-trims", "data"),
    Input("group-visibility", "data"),
)
def update_combined_charts(tab, units, px_data, comp_data,
                           show_baseline, band_pct, fit_settings,
                           test_vis, shape_settings, added_trims,
                           group_vis):
    if tab != "combined":
        return no_update
    show = "on" in (show_baseline or [])
    band = band_pct or 0
    fs = fit_settings or {}
    tv = test_vis or {}
    ss = shape_settings or {}
    at = added_trims or []
    gv = group_vis or {}

    head_fig = _build_chart("head", units or "feet",
                            px_data, comp_data, show, band, fs, tv, ss, at, gv)
    pwr_fig = _build_chart("power", units or "feet",
                           px_data, comp_data, show, band, fs, tv, ss, at, gv)

    # Merge into a shared-x-axis subplot
    from plotly.subplots import make_subplots
    combined = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=("Head vs Flow", "Power vs Flow"),
    )

    # Add head traces to row 1
    for trace in head_fig.data:
        combined.add_trace(trace, row=1, col=1)

    # Add power traces to row 2
    for trace in pwr_fig.data:
        # Avoid duplicate legend entries — hide power trace legends
        trace.showlegend = False
        combined.add_trace(trace, row=2, col=1)

    # Apply y-axis labels
    y_head = ("Head (PSI)" if (units or "feet") == "psi"
              else "Head (ft)")
    combined.update_yaxes(title_text=y_head, row=1, col=1,
                          gridcolor=GRID_COLOR, zeroline=False)
    combined.update_yaxes(title_text="Power (HP)", row=2, col=1,
                          gridcolor=GRID_COLOR, zeroline=False)

    # Shared x-axis label on bottom only
    combined.update_xaxes(title_text="Flow (GPM)", row=2, col=1,
                          gridcolor=GRID_COLOR, zeroline=False,
                          showspikes=True, spikemode="across",
                          spikethickness=1, spikecolor="#aaa")
    combined.update_xaxes(gridcolor=GRID_COLOR, zeroline=False,
                          showspikes=True, spikemode="across",
                          spikethickness=1, spikecolor="#aaa",
                          row=1, col=1)

    combined.update_layout(
        plot_bgcolor=CHART_BG,
        paper_bgcolor="#fff",
        margin=dict(l=50, r=15, t=30, b=45),
        legend=dict(font=dict(size=10),
                    bgcolor="rgba(255,255,255,0.85)",
                    bordercolor="#ddd", borderwidth=1,
                    orientation="v",
                    yanchor="top", y=0.98,
                    xanchor="right", x=0.99),
        hovermode="closest", dragmode="zoom",
    )

    return combined


# =====================================================================
# CHART CONSTRUCTION
# =====================================================================

def _build_chart(chart_type, units, px_data, comp_data,
                 show_baseline, band, fit_settings, test_vis,
                 shape_settings, added_trims=None, group_vis=None):
    """Build a complete Plotly figure for one chart type."""

    y_labels = {
        "head":       ("Head (ft)", "Head (PSI)"),
        "power":      ("Power (HP)", "Power (HP)"),
        "efficiency": ("Efficiency (%)", "Efficiency (%)"),
    }
    y_title = (y_labels[chart_type][1] if units == "psi"
               else y_labels[chart_type][0])

    fig = go.Figure()

    gv = group_vis or {}
    dia_to_gi = {}
    if comp_data:
        for gi, grp in enumerate(comp_data.get("groups", [])):
            try:
                dia_to_gi[float(grp["nominal_diameter"])] = gi
            except (ValueError, TypeError):
                pass

    # -- 1. PX Baseline Curves ----------------------------------------
    if px_data and show_baseline:
        trims = px_data.get("trims", [])
        for ti, trim_dict in enumerate(trims):
            dia = trim_dict["diameter"]

            matched_gi = dia_to_gi.get(dia)
            if matched_gi is not None and not gv.get(str(matched_gi), True):
                continue

            bc = BASELINE_COLOR

            if chart_type == "efficiency":
                flow, vals = _compute_baseline_efficiency(trim_dict)
            else:
                flow, vals = _get_baseline_data(trim_dict, chart_type,
                                                units)
            if flow is None:
                continue

            try:
                from scipy.interpolate import CubicSpline
                cs = CubicSpline(flow, vals, bc_type='natural')
                x_smooth = np.linspace(flow.min(), flow.max(),
                                       SMOOTH_POINTS)
                y_smooth = cs(x_smooth)
                if chart_type == "efficiency":
                    y_smooth = np.clip(y_smooth, 0, 100)
            except Exception:
                x_smooth, y_smooth = flow, vals

            fig.add_trace(go.Scatter(
                x=x_smooth, y=y_smooth, mode="lines",
                name=f'PX {dia}" baseline',
                line=dict(color=bc, width=2),
                legendgroup=f"px_{dia}",
                hovertemplate=(
                    f'PX {dia}"<br>Q=%{{x:.0f}} GPM<br>'
                    f'{y_title}=%{{y:.1f}}<extra></extra>'),
            ))

            # 1a. Create tolerance area -------------------------------------------------
            if band > 0 and chart_type in ("head", "power"):
                y_upper = y_smooth * (1 + band / 100)
                y_lower = y_smooth * (1 - band / 100)
                x_upper = x_smooth * (1 + band / 100)
                x_lower = x_smooth * (1 - band / 100)
                fig.add_trace(go.Scatter(
                    x=np.concatenate([x_upper, x_lower[::-1]]),
                    y=np.concatenate([y_upper, y_lower[::-1]]),
                    fill="toself", fillcolor=BAND_FILL_COLOR,
                    line=dict(color=BAND_LINE_COLOR, width=0.5),
                    name=f'PX {dia}" ±{band}%',
                    legendgroup=f"px_{dia}",
                    showlegend=False, hoverinfo="skip",
                ))

            if chart_type == "efficiency":
                bep_flow = trim_dict.get("bep_flow")
                bep_eff = trim_dict.get("bep_efficiency")

                # Fallback: compute BEP from head + power curves
                if not bep_flow or not bep_eff:
                    eff_flow, eff_vals = _compute_baseline_efficiency(
                        trim_dict)
                    if eff_flow is not None and len(eff_flow) >= 2:
                        peak_idx = np.argmax(eff_vals)
                        bep_flow = float(eff_flow[peak_idx])
                        bep_eff = float(eff_vals[peak_idx])

                if bep_flow and bep_eff:
                    fig.add_trace(go.Scatter(
                        x=[bep_flow], y=[bep_eff], mode="markers",
                        name=f'PX {dia}" BEP',
                        marker=dict(color=bc, size=10, symbol="diamond",
                                    line=dict(width=1.5, color="#fff")),
                        legendgroup=f"px_{dia}", showlegend=False,
                        hovertemplate=(
                            f'PX {dia}" BEP<br>Q={bep_flow:.0f} GPM<br>'
                            f'η={bep_eff:.1f}%<extra></extra>'),
                    ))

    # -- 2. Test data + fit lines --------------------------------------
    if comp_data:
        groups = comp_data.get("groups", [])
        outlier_map = _compute_outlier_tests(groups, px_data, band)
        chart_fit_settings = (fit_settings or {}).get(str(chart_type), {})
        chart_shape_settings = (shape_settings or {}).get(str(chart_type), {})
        for gi, grp in enumerate(groups):
            if not gv.get(str(gi), True):
                continue

            color = GROUP_COLORS[gi % len(GROUP_COLORS)]
            nom_dia = grp["nominal_diameter"]
            tests = grp.get("tests", [])
            group_outliers = outlier_map.get(str(gi), [])

            fs = chart_fit_settings.get(str(gi), {})
            poly_order = int(fs.get("poly_order", 3))
            offset_pct = float(fs.get("offset_pct", 0))
            fit_range_pct = fs.get("bep_range_pct", [0, 100])

            if not isinstance(fit_range_pct, (list, tuple)) or len(fit_range_pct) != 2:
                fit_range_pct = [0, 100]

            range_start_pct = float(fit_range_pct[0])
            range_end_pct = float(fit_range_pct[1])
            ss = chart_shape_settings.get(str(gi), {})
            droop_on = ss.get("droop_enabled", False)
            droop_pct = float(ss.get("droop_pct", 3))
            carryout_on = ss.get("carryout_enabled", False)
            carryout_pct = float(ss.get("carryout_pct", 3))
            spline_on = ss.get("spline_on", False)
            smoothing = float(ss.get("smoothing", 0.3))
            knots = ss.get("knots", [])

            all_flow = []
            all_y = []

            ti_counter = 0
            
            for test in tests:
                if not test.get("has_data"):
                    continue
                vis_key = f"{gi}-{ti_counter}"
                is_visible = test_vis.get(vis_key, True)
                ti_counter += 1

                if chart_type == "efficiency":
                    flow_key, y_key = "scaled_flow", "scaled_efficiency"
                elif chart_type == "head":
                    flow_key, y_key = "scaled_flow", "scaled_head"
                elif chart_type == "power":
                    flow_key, y_key = "scaled_flow", "scaled_power"
                else:
                    flow_key, y_key = "raw_flow", None

                flow_data = test.get(flow_key)
                y_data = test.get(y_key) if y_key else None
                if flow_data is None or y_data is None:
                    continue

                flow_arr = np.array(flow_data, dtype=float)
                y_arr = np.array(y_data, dtype=float)

                if chart_type == "head" and units == "psi":
                    y_arr = y_arr * FEET_TO_PSI

                mask = np.isfinite(flow_arr) & np.isfinite(y_arr)
                flow_arr = flow_arr[mask]
                y_arr = y_arr[mask]

                if chart_type == "efficiency":
                    eff_mask = (y_arr > 0) & (y_arr <= 100)
                    flow_arr = flow_arr[eff_mask]
                    y_arr = y_arr[eff_mask]

                if len(flow_arr) < 1:
                    continue
                if not is_visible:
                    continue

                all_flow.extend(flow_arr.tolist())
                all_y.extend(y_arr.tolist())

                tid = test.get("test_id", "")
                pf = test.get("pass_fail", "")
                is_polished = test.get("is_polished", False)
                marker_sym = "circle"
                if pf.lower() in ("fail", "no", "false"):
                    marker_sym = "x"
                elif is_polished:
                    marker_sym = "diamond"
                is_outlier = (ti_counter - 1) in group_outliers
                marker_opacity = 0.15 if is_outlier else 0.6

                # Polished impellers get a gold border for visibility
                m_line = (dict(width=1.5, color="#DAA520")
                          if is_polished
                          else dict(width=0.5, color="#fff"))
                polished_tag = "  ◆ Polished" if is_polished else ""

                fig.add_trace(go.Scatter(
                    x=flow_arr, y=y_arr, mode="markers",
                    name=f'{tid[:18]}',
                    marker=dict(color=color, size=6 if is_polished else 5,
                                symbol=marker_sym,
                                opacity=marker_opacity,
                                line=m_line),
                    legendgroup=f"grp_{gi}", showlegend=False,
                    hovertemplate=(
                        f'{tid}{polished_tag}'
                        f'{"  ⚠ OUT OF BAND" if is_outlier else ""}<br>'
                        f'Q=%{{x:.0f}} GPM<br>'
                        f'{y_title}=%{{y:.1f}}<extra></extra>'),
                ))
                            # -- Derived efficiency fit -----------------------------
            #
            # Efficiency markers still come from scaled_efficiency,
            # but the line is calculated from the fitted head and
            # fitted power curves selected by the user.
            if chart_type == "efficiency":
                eff_flow, eff_fit = _compute_fit_efficiency(
                    group=grp,
                    group_index=gi,
                    px_data=px_data,
                    fit_settings=fit_settings,
                    shape_settings=shape_settings,
                    test_vis=test_vis,
                )

                if eff_flow is not None and len(eff_flow) >= 2:
                    peak_index = int(np.argmax(eff_fit))
                    peak_flow = float(eff_flow[peak_index])
                    peak_efficiency = float(eff_fit[peak_index])

                    fig.add_trace(go.Scatter(
                        x=eff_flow,
                        y=eff_fit,
                        mode="lines",
                        name=(
                            f'{nom_dia}" calculated efficiency'
                        ),
                        line=dict(
                            color=color,
                            width=2.5,
                            dash="solid",
                        ),
                        legendgroup=f"grp_{gi}",
                        hovertemplate=(
                            f'{nom_dia}" Calculated Efficiency<br>'
                            'Q=%{x:.0f} GPM<br>'
                            'Efficiency=%{y:.1f}%'
                            '<extra></extra>'
                        ),
                    ))

                    # Calculated BEP from the derived efficiency fit.
                    fig.add_trace(go.Scatter(
                        x=[peak_flow],
                        y=[peak_efficiency],
                        mode="markers",
                        name=f'{nom_dia}" calculated BEP',
                        marker=dict(
                            color=color,
                            size=10,
                            symbol="diamond",
                            line=dict(
                                width=1.5,
                                color="#ffffff",
                            ),
                        ),
                        legendgroup=f"grp_{gi}",
                        showlegend=False,
                        hovertemplate=(
                            f'{nom_dia}" Calculated BEP<br>'
                            f'Q={peak_flow:.0f} GPM<br>'
                            f'η={peak_efficiency:.1f}%'
                            '<extra></extra>'
                        ),
                    ))

                # Do not run the ordinary scaled_efficiency fit below.
                continue
            # -- Fit line ------------------------------------------
            if len(all_flow) == 0:
                for ti, trim_dict_fit in enumerate(trims):
                    dia = trim_dict_fit["diameter"]

                    matched_gi = dia_to_gi.get(dia)
                    if matched_gi is not None and matched_gi != gi:
                        continue
                
                    if chart_type == "efficiency":
                        flow, vals = _compute_baseline_efficiency(trim_dict_fit)
                    else:
                        flow, vals = _get_baseline_data(trim_dict_fit, chart_type, units)

                    if flow is None or vals is None:
                        continue
                    

                    fit_f = np.asarray(flow, dtype=float)
                    fit_y = np.asarray(vals, dtype=float)

                    fit_f_min = fit_f.min()
                    fit_f_max = fit_f.max()

                     # Convert slider percentages into actual flow limits

                    min_fit = fit_f_min + (fit_f_max - fit_f_min) * (range_start_pct / 100.0)
                    max_fit = fit_f_min + (fit_f_max - fit_f_min) * (range_end_pct / 100.0)

                    # Only use points inside the selected window

                    fm = (
                        (fit_f >= min_fit) &
                        (fit_f <= max_fit)
                    )

                    y_fit = fit_y[fm]
                    x_fit = fit_f[fm]

                    if spline_on:
                        kf = [k["flow"] for k in knots]
                        kn = [k["nudge_pct"] for k in knots]
                        x_fit, y_fit = _fit_piecewise_spline(
                            fit_f, fit_y, kf, kn, smoothing)
                    else:
                        try:
                            order = min(poly_order, len(fit_f) - 1)
                            coeffs = np.polyfit(fit_f, fit_y, order)
                            x_fit = np.linspace(min_fit, max_fit, 200)
                            y_fit = np.polyval(coeffs, x_fit)
                        except Exception:
                            x_fit = np.sort(fit_f)
                            y_fit = fit_y[np.argsort(fit_f)]

                    # apply user shaping options to baseline curve
                    if offset_pct != 0:
                        y_fit = y_fit * (1 + offset_pct / 100)
                        x_fit = x_fit * (1 + offset_pct / 100)

                    if droop_on and droop_pct != 0 and chart_type in ("head", "power"):
                        fr = x_fit.max() - x_fit.min()
                        y_fit = _apply_droop(
                            x_fit,
                            y_fit,
                            droop_pct,
                            x_fit.min(),
                            fr,
                        )

                    if carryout_on and carryout_pct != 0 and chart_type in ("head", "power"):
                        fr = x_fit.max() - x_fit.min()
                        y_fit = _apply_carryout(
                            x_fit,
                            y_fit,
                            carryout_pct,
                            x_fit.max(),
                            fr,
                        )

                    if chart_type == "efficiency":
                        y_fit = np.clip(y_fit, 0, 100)

                    fig.add_trace(go.Scatter(
                        x=x_fit, y=y_fit, mode="lines",
                        name=f'{nom_dia}" fit',
                        line=dict(color=color, width=2.5, dash="solid"),
                        legendgroup=f"grp_{gi}",
                        hovertemplate=(
                            f'{nom_dia}" Best Fit<br>Q=%{{x:.0f}} GPM<br>'
                            f'{y_title}=%{{y:.1f}}<extra></extra>'),
                    ))

            elif len(all_flow) >= 3:
                all_flow_arr = np.array(all_flow)
                all_y_arr = np.array(all_y)
                data_min = all_flow_arr.min()
                data_max = all_flow_arr.max()

                # Find baseline curve max flow for this group
                baseline_max = data_max
                if px_data:
                    try:
                        nd = float(nom_dia)
                        for pt in px_data.get("trims", []):
                            if abs(pt["diameter"] - nd) < 0.05:
                                hf = pt.get("head_flow", [])
                                if hf:
                                    baseline_max = max(baseline_max,
                                                       max(hf))
                                break
                    except (ValueError, TypeError):
                        print("Not determining baseline max correctly")
                        pass

                # Full range = from data start to whichever is longer:
                # the data or the baseline curve
                full_max = max(data_max, baseline_max)

                # Convert slider percentages into actual flow limits

                min_fit = data_min + (full_max - data_min) * (range_start_pct / 100.0)
                max_fit = data_min + (full_max - data_min) * (range_end_pct / 100.0)

                # Only use points inside the selected window

                fm = (
                    (all_flow_arr >= min_fit) &
                    (all_flow_arr <= max_fit)
                )

                fit_f = all_flow_arr[fm]
                fit_y = all_y_arr[fm]

                if len(fit_f) >= 3:
                    if spline_on:
                        kf = [k["flow"] for k in knots]
                        kn = [k["nudge_pct"] for k in knots]
                        x_fit, y_fit = _fit_piecewise_spline(
                            fit_f, fit_y, kf, kn, smoothing)
                    else:
                        try:
                            order = min(poly_order, len(fit_f) - 1)
                            coeffs = np.polyfit(fit_f, fit_y, order)
                            x_fit = np.linspace(min_fit, max_fit, 200)
                            y_fit = np.polyval(coeffs, x_fit)
                        except Exception:
                            x_fit = np.sort(fit_f)
                            y_fit = fit_y[np.argsort(fit_f)]

                    if offset_pct != 0:
                        y_fit = y_fit * (1 + offset_pct / 100)
                        x_fit = x_fit * (1 + offset_pct / 100)
                    if droop_on and droop_pct != 0 and chart_type in ("head", "power"):
                        fr = x_fit.max() - x_fit.min()
                        y_fit = _apply_droop(x_fit, y_fit, droop_pct,
                                             x_fit.min(), fr)
                    if carryout_on and carryout_pct != 0 and chart_type in ("head", "power"):
                        fr = x_fit.max() - x_fit.min()
                        y_fit = _apply_carryout(x_fit, y_fit, carryout_pct,
                                                x_fit.max(), fr)
                    if chart_type == "efficiency":
                        y_fit = np.clip(y_fit, 0, 100)

                    fig.add_trace(go.Scatter(
                        x=x_fit, y=y_fit, mode="lines",
                        name=f'{nom_dia}" fit (n={len(fit_f)})',
                        line=dict(color=color, width=2.5, dash="solid"),
                        legendgroup=f"grp_{gi}",
                        hovertemplate=(
                            f'{nom_dia}" Best Fit<br>Q=%{{x:.0f}} GPM<br>'
                            f'{y_title}=%{{y:.1f}}<extra></extra>'),
                    ))

    # -- 3. Virtual trim groups ----------------------------------------
    if added_trims and comp_data and px_data and show_baseline:
        _render_virtual_groups(
            fig, chart_type, units, y_title,
            added_trims, comp_data, px_data, band,
            fit_settings, test_vis, gv, shape_settings,
        )

    # -- Layout --------------------------------------------------------
    fig.update_layout(
        xaxis_title="Flow (GPM)",
        yaxis_title=y_title,
        plot_bgcolor=CHART_BG,
        paper_bgcolor="#fff",
        margin=dict(l=50, r=15, t=10, b=45),
        xaxis=dict(gridcolor=GRID_COLOR, zeroline=False,
                   showspikes=True, spikemode="across",
                   spikethickness=1, spikecolor="#aaa"),
        yaxis=dict(gridcolor=GRID_COLOR, zeroline=False,
                   showspikes=True, spikemode="across",
                   spikethickness=1, spikecolor="#aaa"),
        legend=dict(font=dict(size=10),
                    bgcolor="rgba(255,255,255,0.85)",
                    bordercolor="#ddd", borderwidth=1,
                    orientation="v",
                    yanchor="top", y=0.98,
                    xanchor="right", x=0.99),
        hovermode="closest", dragmode="zoom",
    )

    if chart_type == "efficiency":
        fig.update_layout(yaxis=dict(range=[0, 105],
                                     gridcolor=GRID_COLOR,
                                     zeroline=False))
    return fig


# =====================================================================
# COMPUTE PX BASELINE EFFICIENCY
# =====================================================================

def _compute_baseline_efficiency(trim_dict):
    head_flow, head_vals = _get_baseline_data(trim_dict, "head", "feet")
    power_flow, power_vals = _get_baseline_data(trim_dict, "power", "feet")

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

# =====================================================================
# COMPUTE FIT EFFICIENCY
# =====================================================================

def _get_setting_pair(settings, chart_type, group_index):
    """Return fit and shape settings for one chart and group."""
    fit = (
        (settings[0] or {})
        .get(str(chart_type), {})
        .get(str(group_index), {})
    )

    shape = (
        (settings[1] or {})
        .get(str(chart_type), {})
        .get(str(group_index), {})
    )

    return fit, shape


def _get_fit_range(settings):
    """Validate and return a two-value percentage fit range."""
    fit_range = settings.get("bep_range_pct", [0, 100])

    if (
        not isinstance(fit_range, (list, tuple))
        or len(fit_range) != 2
    ):
        return 0.0, 100.0

    try:
        start = float(fit_range[0])
        end = float(fit_range[1])
    except (TypeError, ValueError):
        return 0.0, 100.0

    start = np.clip(start, 0.0, 100.0)
    end = np.clip(end, 0.0, 100.0)

    if end <= start:
        return 0.0, 100.0

    return start, end


def _collect_visible_fit_data(
    group,
    group_index,
    test_vis,
    value_key,
):
    """
    Collect visible test data for a single group.

    Visibility indexing matches the indexing used in _build_chart().
    """
    all_flow = []
    all_values = []

    test_counter = 0

    for test in group.get("tests", []):
        if not test.get("has_data"):
            continue

        vis_key = f"{group_index}-{test_counter}"
        is_visible = test_vis.get(vis_key, True)
        test_counter += 1

        if not is_visible:
            continue

        flow_data = test.get("scaled_flow")
        value_data = test.get(value_key)

        if flow_data is None or value_data is None:
            continue

        flow = np.asarray(flow_data, dtype=float)
        values = np.asarray(value_data, dtype=float)

        valid = (
            np.isfinite(flow)
            & np.isfinite(values)
            & (flow >= 0)
        )

        if value_key == "scaled_power":
            valid &= values > 0

        flow = flow[valid]
        values = values[valid]

        all_flow.extend(flow.tolist())
        all_values.extend(values.tolist())

    return (
        np.asarray(all_flow, dtype=float),
        np.asarray(all_values, dtype=float),
    )


def _get_group_baseline_max(
    px_data,
    nominal_diameter,
    chart_type,
):
    """Return the matching PX baseline's maximum flow."""
    if not px_data:
        return None

    try:
        nominal_diameter = float(nominal_diameter)
    except (TypeError, ValueError):
        return None

    flow_key = (
        "head_flow"
        if chart_type == "head"
        else "power_flow"
    )

    for trim in px_data.get("trims", []):
        try:
            trim_diameter = float(trim.get("diameter"))
        except (TypeError, ValueError):
            continue

        if abs(trim_diameter - nominal_diameter) >= 0.05:
            continue

        flow = trim.get(flow_key, [])

        if not flow:
            return None

        flow = np.asarray(flow, dtype=float)
        flow = flow[np.isfinite(flow)]

        if len(flow) == 0:
            return None

        return float(flow.max())

    return None


def _build_source_fit(
    group,
    group_index,
    chart_type,
    px_data,
    fit_settings,
    shape_settings,
    test_vis,
):
    """
    Build the fitted head or power curve used to derive efficiency.

    Head is always returned in feet.
    Power is always returned in horsepower.
    """
    if chart_type == "head":
        value_key = "scaled_head"
    elif chart_type == "power":
        value_key = "scaled_power"
    else:
        raise ValueError(
            "_build_source_fit only supports head and power"
        )

    flow, values = _collect_visible_fit_data(
        group,
        group_index,
        test_vis,
        value_key,
    )

    if len(flow) < 3:
        return None, None

    fs, ss = _get_setting_pair(
        (fit_settings, shape_settings),
        chart_type,
        group_index,
    )

    try:
        poly_order = int(fs.get("poly_order", 3))
    except (TypeError, ValueError):
        poly_order = 3

    try:
        offset_pct = float(fs.get("offset_pct", 0))
    except (TypeError, ValueError):
        offset_pct = 0.0

    range_start_pct, range_end_pct = _get_fit_range(fs)

    spline_on = bool(ss.get("spline_on", False))

    try:
        smoothing = float(ss.get("smoothing", 0.3))
    except (TypeError, ValueError):
        smoothing = 0.3

    knots = ss.get("knots", []) or []

    droop_on = bool(ss.get("droop_enabled", False))
    carryout_on = bool(ss.get("carryout_enabled", False))

    try:
        droop_pct = float(ss.get("droop_pct", 3))
    except (TypeError, ValueError):
        droop_pct = 3.0

    try:
        carryout_pct = float(ss.get("carryout_pct", 3))
    except (TypeError, ValueError):
        carryout_pct = 3.0

    data_min = float(flow.min())
    data_max = float(flow.max())

    baseline_max = _get_group_baseline_max(
        px_data,
        group.get("nominal_diameter"),
        chart_type,
    )

    if baseline_max is None:
        baseline_max = data_max

    full_max = max(data_max, baseline_max)

    min_fit = data_min + (
        full_max - data_min
    ) * (range_start_pct / 100.0)

    max_fit = data_min + (
        full_max - data_min
    ) * (range_end_pct / 100.0)

    if max_fit <= min_fit:
        return None, None

    fit_mask = (
        (flow >= min_fit)
        & (flow <= max_fit)
    )

    fit_flow = flow[fit_mask]
    fit_values = values[fit_mask]

    if len(fit_flow) < 3:
        return None, None

    if spline_on:
        knot_flows = []
        knot_nudges = []

        for knot in knots:
            try:
                knot_flows.append(float(knot["flow"]))
                knot_nudges.append(float(knot["nudge_pct"]))
            except (KeyError, TypeError, ValueError):
                continue

        x_fit, y_fit = _fit_piecewise_spline(
            fit_flow,
            fit_values,
            knot_flows,
            knot_nudges,
            smoothing,
        )

        # Keep the spline inside the user's selected range.
        range_mask = (
            (x_fit >= min_fit)
            & (x_fit <= max_fit)
        )

        x_fit = x_fit[range_mask]
        y_fit = y_fit[range_mask]

    else:
        try:
            order = min(
                max(poly_order, 1),
                len(fit_flow) - 1,
            )

            coeffs = np.polyfit(
                fit_flow,
                fit_values,
                order,
            )

            x_fit = np.linspace(
                min_fit,
                max_fit,
                SMOOTH_POINTS,
            )

            y_fit = np.polyval(coeffs, x_fit)

        except (
            ValueError,
            TypeError,
            np.linalg.LinAlgError,
        ):
            order = np.argsort(fit_flow)
            x_fit = fit_flow[order]
            y_fit = fit_values[order]

    x_fit = np.asarray(x_fit, dtype=float)
    y_fit = np.asarray(y_fit, dtype=float)

    valid = np.isfinite(x_fit) & np.isfinite(y_fit)
    x_fit = x_fit[valid]
    y_fit = y_fit[valid]

    if len(x_fit) < 2:
        return None, None

    # Preserve the existing offset behavior used by your charts.
    if offset_pct != 0:
        factor = 1 + offset_pct / 100.0
        x_fit = x_fit * factor
        y_fit = y_fit * factor

    if droop_on and droop_pct != 0:
        flow_range = x_fit.max() - x_fit.min()

        if flow_range > 0:
            y_fit = _apply_droop(
                x_fit,
                y_fit,
                droop_pct,
                x_fit.min(),
                flow_range,
            )

    if carryout_on and carryout_pct != 0:
        flow_range = x_fit.max() - x_fit.min()

        if flow_range > 0:
            y_fit = _apply_carryout(
                x_fit,
                y_fit,
                carryout_pct,
                x_fit.max(),
                flow_range,
            )

    order = np.argsort(x_fit)
    x_fit = x_fit[order]
    y_fit = y_fit[order]

    # np.interp expects unique, increasing x values.
    unique_x, unique_indices = np.unique(
        x_fit,
        return_index=True,
    )

    return unique_x, y_fit[unique_indices]


def _compute_fit_efficiency(
    group,
    group_index,
    px_data,
    fit_settings,
    shape_settings,
    test_vis,
):
    """
    Calculate efficiency from the completed head and power fits.

    Q = GPM
    H = feet
    P = horsepower
    """
    head_flow, head_ft = _build_source_fit(
        group=group,
        group_index=group_index,
        chart_type="head",
        px_data=px_data,
        fit_settings=fit_settings,
        shape_settings=shape_settings,
        test_vis=test_vis,
    )

    power_flow, power_hp = _build_source_fit(
        group=group,
        group_index=group_index,
        chart_type="power",
        px_data=px_data,
        fit_settings=fit_settings,
        shape_settings=shape_settings,
        test_vis=test_vis,
    )

    if (
        head_flow is None
        or head_ft is None
        or power_flow is None
        or power_hp is None
    ):
        return None, None

    common_min = max(
        float(head_flow.min()),
        float(power_flow.min()),
    )

    common_max = min(
        float(head_flow.max()),
        float(power_flow.max()),
    )

    if common_max <= common_min:
        return None, None

    common_flow = np.linspace(
        common_min,
        common_max,
        SMOOTH_POINTS,
    )

    head_interp = np.interp(
        common_flow,
        head_flow,
        head_ft,
    )

    power_interp = np.interp(
        common_flow,
        power_flow,
        power_hp,
    )

    with np.errstate(divide="ignore", invalid="ignore"):
        efficiency = (
            common_flow
            * head_interp
            / (3960.0 * power_interp)
            * 100.0
        )

    valid = (
        np.isfinite(common_flow)
        & np.isfinite(head_interp)
        & np.isfinite(power_interp)
        & np.isfinite(efficiency)
        & (common_flow > 0)
        & (head_interp >= 0)
        & (power_interp > 0)
        & (efficiency > 0)
        & (efficiency <= 100)
    )

    if valid.sum() < 2:
        return None, None

    return common_flow[valid], efficiency[valid]

# =====================================================================
# RENDER VIRTUAL TRIM GROUPS
# =====================================================================

def _render_virtual_groups(fig, chart_type, units, y_title,
                           added_trims, comp_data, px_data, band,
                           fit_settings, test_vis, group_vis,
                           shape_settings=None):
    px_trims = px_data.get("trims", [])
    px_diameters = [t["diameter"] for t in px_trims]
    if not px_diameters:
        return

    all_tests = []
    for grp in comp_data.get("groups", []):
        for t in grp.get("tests", []):
            if t.get("has_data"):
                all_tests.append(t)
    chart_fit_settings = (fit_settings or {}).get(str(chart_type), {})
    chart_shape_settings = (shape_settings or {}).get(str(chart_type), {})
    for vi, vt_info in enumerate(added_trims):
        v_dia = float(vt_info["diameter"])
        color = VIRTUAL_COLORS[vi % len(VIRTUAL_COLORS)]
        fit_idx = VIRTUAL_INDEX_OFFSET + vi
        lg = f"vgrp_{vi}"

        if not group_vis.get(str(fit_idx), True):
            continue

        fs = chart_fit_settings.get(str(fit_idx), {})
        poly_order = int(fs.get("poly_order", 3))
        offset_pct = float(fs.get("offset_pct", 0))
        fit_range_pct = fs.get("bep_range_pct", [0, 100])

        if not isinstance(fit_range_pct, (list, tuple)) or len(fit_range_pct) != 2:
            fit_range_pct = [0, 100]

        range_start_pct = float(fit_range_pct[0])
        range_end_pct = float(fit_range_pct[1])
        ss = chart_shape_settings.get(str(fit_idx), {})
        droop_on = ss.get("droop_enabled", False)
        droop_pct = float(ss.get("droop_pct", 3))
        carryout_on = ss.get("carryout_enabled", False)
        carryout_pct = float(ss.get("carryout_pct", 3))
        spline_on = ss.get("spline_on", False)
        smoothing = float(ss.get("smoothing", 0.3))
        knots = ss.get("knots", [])

        nearest_px_dia = min(px_diameters, key=lambda d: abs(d - v_dia))
        nearest_px_trim = None
        for pt in px_trims:
            if abs(pt["diameter"] - nearest_px_dia) < 0.001:
                nearest_px_trim = pt
                break
        if not nearest_px_trim:
            continue

        d_ratio = v_dia / nearest_px_dia
        q_ratio = d_ratio
        h_ratio = d_ratio ** 2
        p_ratio = d_ratio ** 3

        # Scaled baseline (black dashed)
        if chart_type == "efficiency":
            base_flow, base_vals = _compute_baseline_efficiency(nearest_px_trim)
            if base_flow is not None:
                scaled_flow = base_flow * q_ratio
                scaled_vals = base_vals
            else:
                continue
        else:
            base_flow, base_vals = _get_baseline_data(
                nearest_px_trim, chart_type, units)
            if base_flow is None:
                continue
            scaled_flow = base_flow * q_ratio
            if chart_type == "head":
                scaled_vals = base_vals * h_ratio
            elif chart_type == "power":
                scaled_vals = base_vals * p_ratio
            else:
                scaled_vals = base_vals

        try:
            from scipy.interpolate import CubicSpline
            cs = CubicSpline(scaled_flow, scaled_vals, bc_type='natural')
            x_smooth = np.linspace(scaled_flow.min(), scaled_flow.max(),
                                   SMOOTH_POINTS)
            y_smooth = cs(x_smooth)
            if chart_type == "efficiency":
                y_smooth = np.clip(y_smooth, 0, 100)
        except Exception:
            x_smooth, y_smooth = scaled_flow, scaled_vals

        fig.add_trace(go.Scatter(
            x=x_smooth, y=y_smooth, mode="lines",
            name=f'Added {v_dia:.3f}" baseline',
            line=dict(color=BASELINE_COLOR, width=2, dash="dash"),
            legendgroup=lg,
            hovertemplate=(
                f'Added {v_dia:.3f}"<br>Q=%{{x:.0f}} GPM<br>'
                f'{y_title}=%{{y:.1f}}<extra></extra>'),
        ))

        if band > 0 and chart_type in ("head", "power"):
            y_upper = y_smooth * (1 + band / 100)
            y_lower = y_smooth * (1 - band / 100)
            fig.add_trace(go.Scatter(
                x=np.concatenate([x_smooth, x_smooth[::-1]]),
                y=np.concatenate([y_upper, y_lower[::-1]]),
                fill="toself", fillcolor=BAND_FILL_COLOR,
                line=dict(color=BAND_LINE_COLOR, width=0.5),
                legendgroup=lg, showlegend=False, hoverinfo="skip",
            ))

        # Test markers
        virtual_tests = []
        for t in all_tests:
            td = t.get("trim_diameter")
            if td is None:
                continue
            if abs(td - v_dia) < min(abs(td - d) for d in px_diameters):
                virtual_tests.append(t)

        all_vflow = []
        all_vy = []
        ti_counter = 0
        for t in virtual_tests:
            vis_key = f"{fit_idx}-{ti_counter}"
            is_visible = test_vis.get(vis_key, True)
            ti_counter += 1

            if chart_type == "efficiency":
                fd, yd = t.get("scaled_flow"), t.get("scaled_efficiency")
            elif chart_type == "head":
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
                if units == "psi":
                    ya = ya * FEET_TO_PSI
                ya = ya * (rescale ** 2)
            elif chart_type == "power":
                ya = ya * (rescale ** 3)

            m = np.isfinite(fa) & np.isfinite(ya)
            fa, ya = fa[m], ya[m]
            if len(fa) < 1:
                continue
            if not is_visible:
                continue

            all_vflow.extend(fa.tolist())
            all_vy.extend(ya.tolist())

            tid = t.get("test_id", "")
            pf = t.get("pass_fail", "")
            sym = "x" if pf.lower() in ("fail", "no", "false") else "circle"
            fig.add_trace(go.Scatter(
                x=fa, y=ya, mode="markers", name=f'{tid[:18]}',
                marker=dict(color=color, size=5, symbol=sym, opacity=0.7,
                            line=dict(width=0.5, color="#fff")),
                legendgroup=lg, showlegend=False,
                hovertemplate=(
                    f'{tid}<br>Q=%{{x:.0f}} GPM<br>'
                    f'{y_title}=%{{y:.1f}}<extra></extra>'),
            ))

        # Virtual fit line
        if len(all_vflow) >= 3:
            vf = np.array(all_vflow)
            vy = np.array(all_vy)
            vdata_min, vdata_max = vf.min(), vf.max()

            # Baseline max = end of the scaled baseline curve
            v_baseline_max = scaled_flow.max() if len(scaled_flow) > 0 else vdata_max
            v_full_max = max(vdata_max, v_baseline_max)

            # Convert slider percentages into actual flow limits

            vmin_fit = vdata_min + (v_full_max - vdata_min) * (range_start_pct / 100.0)
            vmax_fit = vdata_min + (v_full_max - vdata_min) * (range_end_pct / 100.0)

                # Only use points inside the selected window

            fm = (
                (vf >= vmin_fit) &
                (vf <= vmax_fit)
                )

            vf_fit = vf[fm]               
            vy_fit = vy[fm]

            if len(vf_fit) >= 3:
                try:
                    if spline_on:
                        kf = [k["flow"] for k in knots]
                        kn = [k["nudge_pct"] for k in knots]
                        x_fit, y_fit = _fit_piecewise_spline(
                            vf_fit, vy_fit, kf, kn, smoothing)
                        trunc = (
                            (x_fit >= vmin_fit) & 
                            (x_fit <= vmax_fit)
                            )
                        if np.any(trunc):
                            x_fit = x_fit[trunc]
                            y_fit = y_fit[trunc]
                    else:
                        order = min(poly_order, len(vf_fit) - 1)
                        coeffs = np.polyfit(vf_fit, vy_fit, order)
                        x_fit = np.linspace(vmin_fit, vmax_fit, 200)
                        y_fit = np.polyval(coeffs, x_fit)

                    if offset_pct != 0:
                        y_fit = y_fit * (1 + offset_pct / 100)
                        x_fit = x_fit * (1 + offset_pct / 100)
                    if droop_on and droop_pct != 0 and chart_type in ("head", "power"):
                        fr = x_fit.max() - x_fit.min()
                        y_fit = _apply_droop(x_fit, y_fit, droop_pct,
                                             x_fit.min(), fr)
                    if carryout_on and carryout_pct != 0 and chart_type in ("head", "power"):
                        fr = x_fit.max() - x_fit.min()
                        y_fit = _apply_carryout(x_fit, y_fit, carryout_pct,
                                                x_fit.max(), fr)
                    if chart_type == "efficiency":
                        y_fit = np.clip(y_fit, 0, 100)

                    fig.add_trace(go.Scatter(
                        x=x_fit, y=y_fit, mode="lines",
                        name=f'Added {v_dia:.3f}" fit ({len(vf_fit)} pts)',
                        line=dict(color=color, width=2.5, dash="solid"),
                        legendgroup=lg,
                        hovertemplate=(
                            f'Added {v_dia:.3f}" Fit<br>Q=%{{x:.0f}} GPM<br>'
                            f'{y_title}=%{{y:.1f}}<extra></extra>'),
                    ))
                except Exception:
                    pass


# =====================================================================
# SPLINE FIT WITH KNOT CONTROL
# =====================================================================

def _fit_piecewise_spline(flow, y, knot_flows, knot_nudges,
                          smoothing=0.3):
    idx = np.argsort(flow)
    flow_s = flow[idx]
    y_s = y[idx]

    flow_min = flow_s.min()
    flow_max = flow_s.max()
    flow_range = flow_max - flow_min

    if flow_range <= 0 or len(flow_s) < 3:
        return flow_s, y_s

    n_bins = max(12, len(flow_s) // 8)
    bin_edges = np.linspace(flow_min, flow_max, n_bins + 1)

    rep_x, rep_y, rep_w = [], [], []
    for bi in range(n_bins):
        lo, hi = bin_edges[bi], bin_edges[bi + 1]
        if bi == n_bins - 1:
            mask = (flow_s >= lo) & (flow_s <= hi)
        else:
            mask = (flow_s >= lo) & (flow_s < hi)
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
        local_y = np.median(y_s[nearby]) if nearby.sum() > 0 else np.interp(kf, rep_x, rep_y)
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
# SHUTOFF / CARRYOUT ADJUSTMENT
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
# HELPERS
# =====================================================================

def _get_baseline_data(trim_dict, chart_type, units = "feet"):
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
    if chart_type == "head" and units == "psi":
        vals = vals * FEET_TO_PSI
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


# =====================================================================
# OUTLIER DETECTION
# =====================================================================

def _compute_outlier_tests(groups, px_data, band_pct):
    if not groups or not px_data or not band_pct or band_pct <= 0:
        return {}
    px_trims = px_data.get("trims", [])
    result = {}
    for gi, grp in enumerate(groups):
        try:
            nom_dia = float(grp["nominal_diameter"])
        except (ValueError, TypeError):
            continue
        tests = grp.get("tests", [])
        baseline_trim = None
        for pt in px_trims:
            if abs(pt["diameter"] - nom_dia) < 0.05:
                baseline_trim = pt
                break
        if not baseline_trim:
            continue
        bf = baseline_trim.get("head_flow")
        bh = baseline_trim.get("head")
        if not bf or not bh:
            continue
        bf = np.array(bf, dtype=float)
        bh = np.array(bh, dtype=float)
        si = np.argsort(bf)
        bf, bh = bf[si], bh[si]

        outliers = []
        tc = 0
        for t in tests:
            if not t.get("has_data"):
                continue
            fd = t.get("scaled_flow")
            hd = t.get("scaled_head")
            if fd is None or hd is None:
                tc += 1
                continue
            fa = np.array(fd, dtype=float)
            ha = np.array(hd, dtype=float)
            ir = (fa >= bf.min()) & (fa <= bf.max())
            if not np.any(ir):
                tc += 1
                continue
            fc, hc = fa[ir], ha[ir]
            bl = np.interp(fc, bf, bh)
            if np.any(hc > bl * (1 + band_pct / 100)) or np.any(hc < bl * (1 - band_pct / 100)):
                outliers.append(tc)
            tc += 1
        if outliers:
            result[str(gi)] = outliers
    return result


# =====================================================================
# POPULATE VIRTUAL GROUP CARDS
# =====================================================================

@callback(
    Output("virtual-group-panels", "children"),
    Input("added-trims", "data"),
    Input("comparison-results", "data"),
    Input("chart-tabs", "value"),
    State("px-data", "data"),
    State("fit-settings", "data"),
    State("shape-settings", "data"),
    State("group-visibility", "data"),
    State("test-visibility", "data"),
    prevent_initial_call=True,
)
def populate_virtual_panels(added_trims, comp_data, chart_type, px_data,
                            fit_settings, shape_settings,
                            group_vis, test_vis):
    from dash import html
    from layout.main_layout import build_virtual_trim_card

    if not added_trims or not comp_data or not px_data:
        return []

    px_diameters = [t["diameter"] for t in px_data.get("trims", [])]
    all_tests = []
    for grp in comp_data.get("groups", []):
        for t in grp.get("tests", []):
            if t.get("has_data"):
                all_tests.append(t)

    cards = []
    for vi, vt_info in enumerate(added_trims):
        v_dia = float(vt_info["diameter"])
        virtual_tests = []
        for t in all_tests:
            td = t.get("trim_diameter")
            if td is None:
                continue
            if abs(td - v_dia) < min(abs(td - d) for d in px_diameters):
                virtual_tests.append(t)
        cards.append(build_virtual_trim_card(
            vi, vt_info, virtual_tests,
            chart_type=chart_type,
            fit_settings=fit_settings or {},
            shape_settings=shape_settings or {},
            group_visibility=group_vis or {},
            test_visibility=test_vis or {},
        ))
    return cards

# =====================================================================
# POPULATE TRIM GROUP CARDS
# =====================================================================

@callback(
    Output("trim-group-panels", "children", allow_duplicate=True),
   
    Input("comparison-results", "data"),
    Input("chart-tabs", "value"),

    State("px-data", "data"),
    State("fit-settings", "data"),
    State("shape-settings", "data"),
    State("group-visibility", "data"),
    State("test-visibility", "data"),
    prevent_initial_call=True,
)
def populate_trim_panels(comp_data, chart_type, px_data,
                            fit_settings, shape_settings,
                            group_vis, test_vis):
    from dash import html
    from layout.main_layout import build_trim_group_card

    if not comp_data or not px_data:
        return []
    groups = comp_data.get("groups", [])
    
    chart_fit_settings = (fit_settings or {}).get(str(chart_type), {})
    chart_shape_settings = (shape_settings or {}).get(str(chart_type), {})
    cards = []
    for idx, group in enumerate(groups):
        cards.append(build_trim_group_card(
            idx, group,chart_type,
            fit_settings=chart_fit_settings or {},
            shape_settings=chart_shape_settings or {},
            group_visibility=group_vis or {},
            test_visibility=test_vis or {},
        ))
    return cards


# =====================================================================
# OUTLIER BADGE UPDATE
# =====================================================================

@callback(
    Output({"type": "outlier-badge", "index": ALL}, "children"),
    Input("band-pct", "value"),
    Input("comparison-results", "data"),
    State("px-data", "data"),
    State("added-trims", "data"),
    State({"type": "outlier-badge", "index": ALL}, "id"),
    prevent_initial_call=True,
)
def update_outlier_badges(band_pct, comp_data, px_data,
                          added_trims, badge_ids):
    from dash import html
    if not badge_ids:
        raise PreventUpdate
    band = float(band_pct or 0)
    n = len(badge_ids)
    if not comp_data or not px_data or band <= 0:
        return ["" for _ in range(n)]
    groups = comp_data.get("groups", [])
    outlier_map = _compute_outlier_tests(groups, px_data, band)

    # Compute virtual trim outliers
    if added_trims:
        for vi in range(len(added_trims)):
            gi = VIRTUAL_INDEX_OFFSET + vi
            virtual_outliers = _compute_virtual_outlier_tests(
                gi, added_trims, comp_data, px_data, band)
            outlier_map.update(virtual_outliers)

    results = []
    for bid in badge_ids:
        idx = bid.get("index", -1)
        outliers = outlier_map.get(str(idx), [])
        if outliers:
            results.append(html.Span(f"⚠ {len(outliers)} out of band",
                                     className="outlier-badge-warn"))
        else:
            results.append(html.Span("✓ All within band",
                                     className="outlier-badge-ok"))
    return results


# =====================================================================
# AUTO-CLEAN — uncheck outlier tests
# =====================================================================

@callback(
    Output({"type": "test-vis", "index": ALL}, "value",
           allow_duplicate=True),
    Input({"type": "auto-clean-btn", "index": ALL}, "n_clicks"),
    State("comparison-results", "data"),
    State("px-data", "data"),
    State("band-pct", "value"),
    State("added-trims", "data"),
    State({"type": "test-vis", "index": ALL}, "value"),
    State({"type": "test-vis", "index": ALL}, "id"),
    prevent_initial_call=True,
)
def auto_clean_outliers(all_clicks, comp_data, px_data, band_pct,
                        added_trims, current_vis_values, vis_ids):
    if not ctx.triggered_id:
        raise PreventUpdate
    tid = ctx.triggered_id
    if not isinstance(tid, dict) or tid.get("type") != "auto-clean-btn":
        raise PreventUpdate
    if not any(c and c > 0 for c in (all_clicks or [])):
        raise PreventUpdate
    gi = tid["index"]
    band = float(band_pct or 0)
    if not comp_data or not px_data or band <= 0:
        raise PreventUpdate

    groups = comp_data.get("groups", [])

    # Compute outliers — includes virtual trims if added_trims exists
    outlier_map = _compute_outlier_tests(groups, px_data, band)

    if gi >= VIRTUAL_INDEX_OFFSET and added_trims:
        # Virtual trim — compute outliers against affinity-scaled baseline
        virtual_outliers = _compute_virtual_outlier_tests(
            gi, added_trims, comp_data, px_data, band)
        outlier_map.update(virtual_outliers)

    outlier_indices = set(outlier_map.get(str(gi), []))
    if not outlier_indices:
        raise PreventUpdate
    new_values = list(current_vis_values)
    for i, vid in enumerate(vis_ids):
        idx_str = vid.get("index", "")
        parts = idx_str.split("-")
        if len(parts) != 2:
            continue
        try:
            if int(parts[0]) == gi and int(parts[1]) in outlier_indices:
                new_values[i] = []
        except (ValueError, TypeError):
            continue
    return new_values


def _compute_virtual_outlier_tests(gi, added_trims, comp_data, px_data,
                                   band_pct):
    """Compute outlier test indices for a virtual trim group."""
    vi = gi - VIRTUAL_INDEX_OFFSET
    if vi < 0 or vi >= len(added_trims):
        return {}

    vt_info = added_trims[vi]
    v_dia = float(vt_info["diameter"])

    px_trims = px_data.get("trims", [])
    px_diameters = [t["diameter"] for t in px_trims]
    if not px_diameters:
        return {}

    # Find nearest PX trim and compute affinity-scaled baseline
    nearest_px_dia = min(px_diameters, key=lambda d: abs(d - v_dia))
    nearest_px_trim = None
    for pt in px_trims:
        if abs(pt["diameter"] - nearest_px_dia) < 0.001:
            nearest_px_trim = pt
            break
    if not nearest_px_trim:
        return {}

    bf = nearest_px_trim.get("head_flow")
    bh = nearest_px_trim.get("head")
    if not bf or not bh:
        return {}

    bf = np.array(bf, dtype=float)
    bh = np.array(bh, dtype=float)

    # Scale baseline to virtual diameter
    d_ratio = v_dia / nearest_px_dia
    scaled_bf = bf * d_ratio
    scaled_bh = bh * (d_ratio ** 2)
    si = np.argsort(scaled_bf)
    scaled_bf, scaled_bh = scaled_bf[si], scaled_bh[si]

    # Gather virtual tests (same logic as chart rendering)
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

    outliers = []
    tc = 0
    for t in virtual_tests:
        if not t.get("has_data"):
            continue
        fd = t.get("scaled_flow")
        hd = t.get("scaled_head")
        if fd is None or hd is None:
            tc += 1
            continue

        fa = np.array(fd, dtype=float)
        ha = np.array(hd, dtype=float)

        # Rescale test data to virtual diameter
        t_trim = t.get("trim_diameter", v_dia)
        rescale = v_dia / t_trim if t_trim > 0 else 1.0
        fa = fa * rescale
        ha = ha * (rescale ** 2)

        ir = (fa >= scaled_bf.min()) & (fa <= scaled_bf.max())
        if not np.any(ir):
            tc += 1
            continue
        fc, hc = fa[ir], ha[ir]
        bl = np.interp(fc, scaled_bf, scaled_bh)
        if (np.any(hc > bl * (1 + band_pct / 100))
                or np.any(hc < bl * (1 - band_pct / 100))):
            outliers.append(tc)
        tc += 1

    result = {}
    if outliers:
        result[str(gi)] = outliers
    return result

# =====================================================================
# FILTER TEST VISIBILITY BY ITERATION
# =====================================================================

@callback(
    Output({"type": "test-vis", "index": ALL}, "value",
           allow_duplicate=True),
    Input({"type": "iteration-selector", "index": ALL}, "value"),
    State("comparison-results", "data"),
    State({"type": "test-vis", "index": ALL}, "value"),
    State({"type": "test-vis", "index": ALL}, "id"),
    prevent_initial_call=True,
)
def filter_by_iteration(slider_value, comp_data, current_vis_values, vis_ids):
    if not ctx.triggered_id:
        raise PreventUpdate
    tid = ctx.triggered_id
    if not isinstance(tid, dict) or tid.get("type") != "iteration-selector":
        raise PreventUpdate
    triggered_gi = tid["index"]
    if slider_value is None or not comp_data:
        raise PreventUpdate

    new_values = list(current_vis_values)
    iteration_map = {}

    for gi, group in enumerate(comp_data.get("groups", [])):
        print(f"Processing group {gi} with {len(group.get('tests', []))} tests")
        for ti, test in enumerate(group.get("tests", [])):
            print(f"Processing test {ti} in group {gi}")
            test_id = test.get("test_id", "")

            m = re.search(r"([A-Z])(\d+)P?$", test_id)
            iteration_map[(gi, ti)] = int(m.group(2)) if m else None
            print(f"Mapped test_id {test_id} to iteration {iteration_map[(gi, ti)]}")
    for i, vid in enumerate(vis_ids):
        print(f"Checking visibility for test index {vid}")
        idx_str = vid.get("index", "")
        parts = idx_str.split("-")

        if len(parts) != 2:
            continue
        print(f"Parsed index string '{idx_str}' into parts: {parts}")
        try:
            group_idx = int(parts[0])
            test_idx = int(parts[1])

            if group_idx != triggered_gi:
                continue
            iteration = iteration_map.get((group_idx, test_idx))
            selected_iteration = slider_value[triggered_gi]

            if selected_iteration == 3:
                visible = True
            else:
                visible = (
                    iteration is not None
                    and iteration <= selected_iteration
                )

            new_values[i] = ["on"] if visible else []

        except (ValueError, TypeError, IndexError, KeyError):
            continue

    return new_values




# =====================================================================
# MANUAL MODE — FIT QUALITY ANALYSIS
# =====================================================================

def _build_manual_analysis(comp_data, fit_settings, shape_settings,
                           test_vis, chart_type):
    """Build analysis panels for manual mode with fit quality stats."""
    from dash import html

    groups = comp_data.get("groups", [])
    if not groups:
        return [html.P("No test data to analyze.",
                        className="empty-message")]

    children = []

    # Header
    children.append(html.Div(className="analysis-section", children=[
        html.Div("Manual Mode — Curve Fit Analysis",
                 className="analysis-title"),
        html.Div("Statistics for each active trim group's best-fit "
                 "line based on current settings and visible tests.",
                 className="hint-text"),
    ]))
    chart_fit_settings = (fit_settings or {}).get(str(chart_type), {})
    chart_shape_settings = (shape_settings or {}).get(str(chart_type), {})
    for gi, grp in enumerate(groups):
        nom_dia = grp.get("nominal_diameter", f"Group {gi}")
        tests = grp.get("tests", [])
        color = GROUP_COLORS[gi % len(GROUP_COLORS)]

        fs = chart_fit_settings.get(str(gi), {})
        poly_order = int(fs.get("poly_order", 3))
        offset_pct = float(fs.get("offset_pct", 0))

        ss = chart_shape_settings.get(str(gi), {})
        spline_on = ss.get("spline_on", False)

        # Gather visible test data for each chart type
        chart_stats = {}
        for chart_type, flow_key, y_key, label in [
            ("head", "scaled_flow", "scaled_head", "Head (ft)"),
            ("power", "scaled_flow", "scaled_power", "Power (HP)"),
            ("efficiency", "scaled_flow", "scaled_efficiency",
             "Efficiency (%)"),
        ]:
            all_f, all_y = [], []
            ti_counter = 0
            for t in tests:
                if not t.get("has_data"):
                    continue
                vis_key = f"{gi}-{ti_counter}"
                is_visible = test_vis.get(vis_key, True)
                ti_counter += 1
                if not is_visible:
                    continue

                fd = t.get(flow_key)
                yd = t.get(y_key)
                if fd is None or yd is None:
                    continue

                fa = np.array(fd, dtype=float)
                ya = np.array(yd, dtype=float)
                mask = np.isfinite(fa) & np.isfinite(ya)
                if chart_type == "efficiency":
                    mask = mask & (ya > 0) & (ya <= 100)
                all_f.extend(fa[mask].tolist())
                all_y.extend(ya[mask].tolist())

            if len(all_f) < 3:
                chart_stats[chart_type] = {
                    "label": label, "n": len(all_f),
                    "r2": None, "rmse": None, "mae": None,
                    "max_dev": None, "max_pct": None,
                }
                continue

            fa = np.array(all_f)
            ya = np.array(all_y)

            # Compute fit (same as chart callback)
            try:
                order = min(poly_order, len(fa) - 1)
                coeffs = np.polyfit(fa, ya, order)
                y_pred = np.polyval(coeffs, fa)
            except Exception:
                chart_stats[chart_type] = {
                    "label": label, "n": len(fa),
                    "r2": None, "rmse": None, "mae": None,
                    "max_dev": None, "max_pct": None,
                }
                continue

            if offset_pct != 0:
                y_pred = y_pred * (1 + offset_pct / 100)
                fa = fa * (1 + offset_pct / 100)

            # R²
            ss_res = np.sum((ya - y_pred) ** 2)
            ss_tot = np.sum((ya - np.mean(ya)) ** 2)
            r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

            # RMSE
            rmse = np.sqrt(np.mean((ya - y_pred) ** 2))

            # MAE
            mae = np.mean(np.abs(ya - y_pred))

            # Max absolute deviation
            abs_dev = np.abs(ya - y_pred)
            max_dev = np.max(abs_dev)

            # Max % deviation (avoid divide by zero)
            with np.errstate(divide="ignore", invalid="ignore"):
                pct_dev = np.abs((ya - y_pred) / ya) * 100
                pct_dev = pct_dev[np.isfinite(pct_dev)]
            max_pct = np.max(pct_dev) if len(pct_dev) > 0 else None

            chart_stats[chart_type] = {
                "label": label, "n": len(fa),
                "r2": r2, "rmse": rmse, "mae": mae,
                "max_dev": max_dev, "max_pct": max_pct,
                "mean_y": np.mean(ya),
                "std_y": np.std(ya),
                "coeffs": coeffs.tolist(),
                "order": order,
            }

        # Build the card for this group
        total_tests = sum(1 for t in tests if t.get("has_data"))
        visible_tests = 0
        ti_counter = 0
        for t in tests:
            if not t.get("has_data"):
                continue
            vis_key = f"{gi}-{ti_counter}"
            if test_vis.get(vis_key, True):
                visible_tests += 1
            ti_counter += 1

        card_children = [
            html.Div(className="analysis-title",
                     style={"color": color, "borderBottom":
                            f"2px solid {color}",
                            "paddingBottom": "4px"},
                     children=f"{nom_dia}"),
            html.Div(f"Tests: {visible_tests} visible / "
                     f"{total_tests} total  ·  "
                     f"Poly order: {poly_order}"
                     f"{'  ·  Spline mode' if spline_on else ''}",
                     className="analysis-text",
                     style={"marginBottom": "6px"}),
        ]

        for ct in ("head", "power", "efficiency"):
            s = chart_stats.get(ct, {})
            label = s.get("label", ct)
            n = s.get("n", 0)

            if n < 3:
                card_children.append(html.Div(
                    className="analysis-stat-row",
                    children=[
                        html.Span(label,
                                  className="analysis-stat-label"),
                        html.Span(f"Insufficient data (n={n})",
                                  style={"color": "#999",
                                         "fontSize": "11px"}),
                    ]))
                continue

            r2 = s.get("r2")
            rmse = s.get("rmse")
            mae = s.get("mae")
            max_dev = s.get("max_dev")
            max_pct = s.get("max_pct")

            # R² quality indicator
            if r2 is not None:
                if r2 >= 0.99:
                    r2_cls = "stat-excellent"
                elif r2 >= 0.95:
                    r2_cls = "stat-good"
                elif r2 >= 0.90:
                    r2_cls = "stat-fair"
                else:
                    r2_cls = "stat-poor"
            else:
                r2_cls = "stat-poor"

            stat_items = []
            if r2 is not None:
                stat_items.append(
                    html.Span(f"R²={r2:.6f}",
                              className=f"stat-badge {r2_cls}"))
            if rmse is not None:
                stat_items.append(
                    html.Span(f"RMSE={rmse:.3f}"))
            if mae is not None:
                stat_items.append(
                    html.Span(f"MAE={mae:.3f}"))
            if max_dev is not None:
                stat_items.append(
                    html.Span(f"MaxDev={max_dev:.3f}"))
            if max_pct is not None:
                stat_items.append(
                    html.Span(f"Max%={max_pct:.1f}%"))

            stat_items.append(html.Span(f"n={n}",
                                        style={"color": "#999"}))

            card_children.append(html.Div(
                className="analysis-stat-row",
                children=[
                    html.Span(label,
                              className="analysis-stat-label"),
                    html.Div(className="analysis-stat-values",
                             children=stat_items),
                ]))

        children.append(html.Div(
            className="analysis-section",
            style={"borderLeft": f"3px solid {color}",
                   "paddingLeft": "8px"},
            children=card_children))

    return children


# =====================================================================
# POPULATE TRIM ANALYSIS DRAWER
# =====================================================================

@callback(
    Output("analysis-panels", "children"),
    Input("comparison-results", "data"),
    Input("added-trims", "data"),
    Input("fit-settings", "data"),
    Input("shape-settings", "data"),
    Input("test-visibility", "data"),
    Input("chart-tabs", "value"),
    State("px-data", "data"),
    State("trim-tolerance", "value"),
    prevent_initial_call=True,
)
def populate_analysis_panels(comp_data, added_trims, fit_settings,
                             shape_settings, test_vis, chart_type,
                             px_data, tolerance_pct):
    from dash import html

    if not comp_data:
        return [html.P("Run a comparison first.",
                        className="empty-message")]

    # ── Manual mode: fit quality stats ────────────────────────
    is_manual = comp_data.get("manual_mode", False)
    if is_manual or not px_data:
        return _build_manual_analysis(
            comp_data, fit_settings or {}, shape_settings or {},
            test_vis or {}, chart_type)

    # ── Auto mode: existing PX analysis (unchanged below) ─────
    groups = comp_data.get("groups", [])
    px_trims_list = px_data.get("trims", [])
    px_diameters = sorted([t["diameter"] for t in px_trims_list],
                          reverse=True)
    tolerance = float(tolerance_pct or 5)
    added = added_trims or []
    added_dias = [a["diameter"] for a in added]

    all_trims = []
    for grp in groups:
        for t in grp.get("tests", []):
            td = t.get("trim_diameter")
            if td:
                all_trims.append(td)

    if not all_trims:
        return [html.P("No test data to analyze.",
                        className="empty-message")]

    all_trims_arr = np.array(all_trims)
    children = []

    # Summary
    children.append(html.Div(className="analysis-section", children=[
        html.Div("Test Data Summary", className="analysis-title"),
        html.Div(f"Total tests: {len(all_trims)}", className="analysis-text"),
        html.Div(f"Trim range: {all_trims_arr.min():.3f}\" — "
                 f"{all_trims_arr.max():.3f}\"", className="analysis-text"),
        html.Div(f"Mean: {all_trims_arr.mean():.3f}\"  |  "
                 f"Unique: {len(np.unique(np.round(all_trims_arr, 3)))}  |  "
                 f"Window: ±{tolerance}%", className="analysis-text"),
    ]))

    # Coverage
    baseline_counts = {dia: 0 for dia in px_diameters}
    for t in all_trims:
        nearest = min(px_diameters, key=lambda d: abs(d - t))
        baseline_counts[nearest] += 1

    cov_rows = []
    for dia in px_diameters:
        count = baseline_counts[dia]
        pct = (count / len(all_trims) * 100) if all_trims else 0
        g_trims = [t for t in all_trims
                    if min(px_diameters, key=lambda d: abs(d - t)) == dia]
        avg_corr = np.mean([abs(t - dia) / dia * 100 for t in g_trims]) if g_trims else 0
        cov_rows.append(html.Div(className="coverage-row", children=[
            html.Span(f'{dia:.3f}"', className="coverage-dia"),
            html.Div(className="coverage-bar-bg", children=[
                html.Div(className="coverage-bar-fill",
                         style={"width": f"{max(2, min(100, int(pct)))}%"}),
            ]),
            html.Span(f"{count} tests  avg: {avg_corr:.1f}%",
                      className="coverage-count"),
        ]))

    children.append(html.Div(className="analysis-section", children=[
        html.Div("PX Baseline Coverage", className="analysis-title"),
        *cov_rows,
    ]))

    # Suggestions
    candidates = []
    for i in range(len(px_diameters) - 1):
        upper, lower = px_diameters[i], px_diameters[i + 1]
        between = [t for t in all_trims if lower < t < upper]
        if len(between) < 2:
            continue
        ba = np.array(between)
        center = np.median(ba)
        corrs = np.abs(ba - center) / center * 100
        candidates.append({
            "diameter": round(center, 3), "count": len(between),
            "avg_correction": round(np.mean(corrs), 2),
            "max_correction": round(np.max(corrs), 2),
            "between": f'{upper:.3f}" — {lower:.3f}"',
            "score": len(between) / (1 + np.mean(corrs)),
        })

    for label, tests_list in [
        (f'above {px_diameters[0]:.3f}"',
         [t for t in all_trims if t > px_diameters[0]]),
        (f'below {px_diameters[-1]:.3f}"',
         [t for t in all_trims if t < px_diameters[-1]]),
    ]:
        if len(tests_list) >= 2:
            a = np.array(tests_list)
            c = np.median(a)
            cr = np.abs(a - c) / c * 100
            candidates.append({
                "diameter": round(c, 3), "count": len(tests_list),
                "avg_correction": round(np.mean(cr), 2),
                "max_correction": round(np.max(cr), 2),
                "between": label,
                "score": len(tests_list) / (1 + np.mean(cr)),
            })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    top_3 = candidates[:3]

    if top_3:
        sugg = []
        for ci, cand in enumerate(top_3):
            already = any(abs(cand["diameter"] - ad) < 0.01 for ad in added_dias)
            btn = (html.Span("✓ Added", className="suggestion-added-badge")
                   if already else
                   html.Button("Add Curve",
                               id={"type": "add-trim-btn",
                                   "index": f'{cand["diameter"]:.3f}'},
                               n_clicks=0, className="btn-add-trim"))
            sugg.append(html.Div(className="suggestion-card", children=[
                html.Div(className="suggestion-header", children=[
                    html.Span(f'#{ci+1}  —  {cand["diameter"]:.3f}"',
                              className="suggestion-dia"), btn,
                ]),
                html.Div(f'{cand["count"]} tests  ·  {cand["between"]}',
                         className="analysis-text"),
                html.Div(f'Avg: {cand["avg_correction"]:.1f}%  ·  '
                         f'Max: {cand["max_correction"]:.1f}%',
                         className="analysis-text"),
            ]))
        children.append(html.Div(className="analysis-section", children=[
            html.Div("Suggested Intermediate Curves", className="analysis-title"),
            html.Div("Click 'Add Curve' to create an affinity-scaled baseline.",
                     className="hint-text"),
            *sugg,
        ]))
    else:
        children.append(html.Div(className="analysis-section", children=[
            html.Div("Coverage", className="analysis-title"),
            html.Div("✓ Good coverage — no intermediate curves needed.",
                     className="status-good", style={"padding": "6px 8px"}),
        ]))

    if added:
        ac = [html.Div(className="added-trim-row", children=[
            html.Span(f'{a["diameter"]:.3f}" (from {a["nearest_px"]:.3f}")',
                      className="added-trim-label"),
            html.Button("✗ Remove",
                        id={"type": "remove-trim-btn", "index": ai},
                        n_clicks=0, className="btn-remove-trim"),
        ]) for ai, a in enumerate(added)]
        children.append(html.Div(className="analysis-section", children=[
            html.Div("Active Added Curves", className="analysis-title"),
            *ac,
        ]))

    return children

# =====================================================================
# POPULATE MACHINE LEARNING DRAWER
# =====================================================================

@callback(
    Output("ml-panels", "children"),
    Input("comparison-results", "data"),
    Input("fit-settings", "data"),
    Input("shape-settings", "data"),
    Input("test-visibility", "data"),
    Input("unit-toggle", "value"),
    State("px-data", "data"),
    State("trim-tolerance", "value"),
    prevent_initial_call=True,
)
def populate_ml_panels(comp_data, fit_settings,
                             shape_settings, test_vis,units,
                             px_data, tolerance_pct):
    from dash import html, dcc
    
    curve_data = {}
    groups = comp_data.get("groups", []) if comp_data else []
    trims = px_data.get("trims", []) if px_data else []

    if comp_data:
        for gi, grp in enumerate(groups):

            dia = float(grp["nominal_diameter"])

            head_flow, head =_build_source_fit(group=grp,
                                       group_index=gi,
                                       chart_type="head",
                                       px_data=px_data,
                                       fit_settings=fit_settings,
                                       shape_settings=shape_settings,
                                       test_vis=test_vis,)
            power_flow, power =_build_source_fit(group=grp,
                                         group_index=gi,
                                         chart_type="power",
                                         px_data=px_data,
                                         fit_settings=fit_settings,
                                         shape_settings=shape_settings,
                                         test_vis=test_vis,)
            
            if head_flow is None or head is None or power_flow is None or power is None:
                continue  # Skip if any data is missing
            raw_data = {}
            for ti, test in enumerate(grp.get("tests", [])):
                if not test.get("has_data"):
                    continue
                vis_key = f"{gi}-{ti}"
                is_visible = test_vis.get(vis_key, True)
                if not is_visible:
                    continue
                test_date = str(test.get("test_date", ""))[:10]
                test_flow = test.get("scaled_flow", [])
                test_head = test.get("scaled_head", [])
                test_power = test.get("scaled_power", [])
                raw_data[ti] = {
                    "test_date": test_date,
                    "flow": test_flow,
                    "head": test_head,
                    "power": test_power,
                }

            curve_data[dia] = {
                "group_index": gi,
                "head_flow": head_flow,
                "head": head,
                "power_flow": power_flow,
                "power": power,
                "raw_data": raw_data,
            }
        for trim_dict in trims:
            dia = trim_dict["diameter"]
            eff_flow, eff_vals = _compute_baseline_efficiency(
                        trim_dict)
            if eff_flow is not None and len(eff_flow) >= 2:
                peak_idx = np.argmax(eff_vals)
                bep_flow = float(eff_flow[peak_idx])
            if dia not in curve_data:
                continue  # Skip if no matching group found
            
            base_head_flow, base_head = _get_baseline_data(trim_dict=trim_dict, chart_type="head", units=units)
            base_power_flow, base_power = _get_baseline_data(trim_dict=trim_dict, chart_type="power")
            
            curve_data[dia]["base_head_flow"] = base_head_flow
            curve_data[dia]["base_head"] = base_head
            curve_data[dia]["base_power_flow"] = base_power_flow
            curve_data[dia]["base_power"] = base_power
            curve_data[dia]["bep_flow"] = bep_flow
        if curve_data:
            residuals = _compute_residuals(curve_data)
            children = []
            # ==============================================================
            # Build trend figure
            # ==============================================================

            fig = go.Figure()

            total_trims = 0
            worst_shift = None

            for dia in curve_data:

                classification = classify_failure_modes(
                    q=np.array(residuals[dia]["fit"]["head"]["flow"]),
                    head_residuals=np.array(residuals[dia]["fit"]["head"]["residuals"]),
                    power_residuals=np.array(residuals[dia]["fit"]["power"]["residuals"]),
                    bep_flow=curve_data[dia]["bep_flow"],)

                head_shift = get_residuals_shift(
                    residuals,
                    dia,
                    chart_type="head",
                )

                if len(head_shift) < 2:
                    continue

                total_trims += 1

                df = pd.DataFrame(head_shift)

                dates = pd.to_datetime(df["date"])
                y = df["mean_residual"].to_numpy()

                if len(y):
                    trim_worst = float(np.min(y))

                    if worst_shift is None:
                        worst_shift = trim_worst
                    else:
                        worst_shift = min(
                            worst_shift,
                            trim_worst,
                        )

                days = (
                    dates - dates.min()
                ).dt.days.to_numpy()

                slope, intercept = np.polyfit(
                    days,
                    y,
                    1,
                )

                fit_y = slope * days + intercept

                # Actual points
                fig.add_trace(
                    go.Scatter(
                        x=dates,
                        y=y,
                        mode="markers",
                        marker=dict(size=7),
                        showlegend=False,
                        hovertemplate=
                        f'{dia:.3f}"<br>'
                        'Date=%{x}<br>'
                        'Residual=%{y:.2f}<extra></extra>',
                    )
                )

                # Best-fit line
                fig.add_trace(
                    go.Scatter(
                        x=dates,
                        y=fit_y,
                        mode="lines",
                        line=dict(width=3),
                        name=f'{dia:.3f}"',
                    )
                )

            fig.add_hline(
                y=-3,
                line_dash="dash",
                line_color="red",
                annotation_text="-3 Threshold",
            )

            fig.update_layout(
                title="Residual Shift Trend Analysis",
                template="plotly_white",
                hovermode="x unified",
                margin=dict(
                    l=20,
                    r=20,
                    t=40,
                    b=20,
                ),
                xaxis_title="Test Date",
                yaxis_title="Mean Residual",
                legend_title="Trim",
            )

            # ==============================================================
            # Summary Section
            # ==============================================================

            children.append(
                html.Div(
                    className="analysis-section",
                    children=[
                        html.Div(
                            "Residual Shift Summary",
                            className="analysis-title",
                        ),

                        html.Div(
                            f"Analyzed Trims: {total_trims}",
                            className="analysis-text",
                        ),

                        html.Div(
                            "Threshold: -3.0",
                            className="analysis-text",
                        ),

                        html.Div(
                            f"Worst Observed Shift: "
                            f"{worst_shift:.2f}"
                            if worst_shift is not None
                            else "Worst Observed Shift: N/A",
                            className="analysis-text",
                        ),
                    ],
                )
            )

            # ==============================================================
            # Trend Plot Section
            # ==============================================================

            children.append(
                html.Div(
                    className="analysis-section",
                    children=[
                        html.Div(
                            "Residual Trend Analysis",
                            className="analysis-title",
                        ),

                        dcc.Graph(
                            figure=fig,
                            config={
                                "displayModeBar": False
                            },
                        ),
                    ],
                )
            )

            # ==============================================================
            # Per-Trim Analysis
            # ==============================================================

            for dia in sorted(curve_data.keys(), reverse=True):

                head_shift = get_residuals_shift(
                    residuals,
                    dia,
                    chart_type="head",
                )

                if len(head_shift) < 2:
                    continue

                kpis = get_residual_kpis(
                    residuals,
                    dia,
                    "head",
                )

                forecast = forecast_threshold_date(
                    residuals,
                    dia,
                    "head",
                    threshold=-3.0,
                )

                dates = [
                    datetime.strptime(
                        x["date"],
                        "%Y-%m-%d",
                    )
                    for x in head_shift
                ]

                values = np.array(
                    [
                        x["mean_residual"]
                        for x in head_shift
                    ]
                )

                days = np.array(
                    [
                        (d - dates[0]).days
                        for d in dates
                    ]
                )

                slope, _ = np.polyfit(
                    days,
                    values,
                    1,
                )

                forecast_text = "N/A"
                threshold_class = "analysis-text"

                if forecast:

                    if forecast["already_exceeded"]:

                        forecast_text = (
                            "Threshold exceeded"
                        )

                        threshold_class = (
                            "status-warning"
                        )

                    else:

                        forecast_text = (
                            f"Projected crossing: "
                            f"{forecast['predicted_date']}"
                        )

                        threshold_class = (
                            "status-good"
                        )

                children.append(
                    html.Div(
                        className="analysis-section",
                        children=[

                            html.Div(
                                f'{dia:.3f}" Trim',
                                className="analysis-title",
                            ),

                            html.Div(
                                f"Current Shift: "
                                f"{kpis['current_shift']:.2f}",
                                className="analysis-text",
                            ),

                            html.Div(
                                f"Worst Shift: "
                                f"{kpis['worst_shift']:.2f}",
                                className="analysis-text",
                            ),

                            html.Div(
                                f"Average Shift: "
                                f"{kpis['average_shift']:.2f}",
                                className="analysis-text",
                            ),

                            html.Div(
                                f"Drift Rate: "
                                f"{slope:.4f}/day",
                                className="analysis-text",
                            ),

                            html.Div(
                                forecast_text,
                                className=threshold_class,
                                style={
                                    "padding": "6px 8px"
                                },
                            ),
                            html.Div([
                                html.H5(
                                    classification["mode"]
                                    .replace("_", " ")
                                    .title()),
                                    html.P(
                                        f"{classification['confidence']:.0%} Confidence"),],
                                        className="kpi-card",),
                        ],
                    )
                )

            return children

        return [
            html.P(
                "No data available for ML analysis.",
                className="empty-message",
            )
        ]

# =====================================================================
# ADD / REMOVE VIRTUAL TRIMS
# =====================================================================

@callback(
    Output("added-trims", "data"),
    Input({"type": "add-trim-btn", "index": ALL}, "n_clicks"),
    Input({"type": "remove-trim-btn", "index": ALL}, "n_clicks"),
    State("added-trims", "data"),
    State("px-data", "data"),
    prevent_initial_call=True,
)
def manage_added_trims(add_clicks, remove_clicks, current_added, px_data):
    if not ctx.triggered_id:
        raise PreventUpdate
    current = list(current_added or [])
    px_diameters = [t["diameter"] for t in px_data.get("trims", [])] if px_data else []
    tid = ctx.triggered_id

    if isinstance(tid, dict) and tid.get("type") == "add-trim-btn":
        if not any(c and c > 0 for c in (add_clicks or [])):
            raise PreventUpdate
        try:
            dia = float(tid["index"])
        except (ValueError, TypeError):
            raise PreventUpdate
        if any(abs(a["diameter"] - dia) < 0.01 for a in current):
            raise PreventUpdate
        nearest_px = min(px_diameters, key=lambda d: abs(d - dia)) if px_diameters else dia
        current.append({"diameter": dia, "nearest_px": nearest_px})
        return current

    if isinstance(tid, dict) and tid.get("type") == "remove-trim-btn":
        if not any(c and c > 0 for c in (remove_clicks or [])):
            raise PreventUpdate
        idx = tid["index"]
        if isinstance(idx, int) and 0 <= idx < len(current):
            current.pop(idx)
        return current

    raise PreventUpdate


# =====================================================================
# SHOW ALL TESTS (global + per-card)
# =====================================================================

@callback(
    Output({"type": "test-vis", "index": ALL}, "value",
           allow_duplicate=True),
    Input("show-all-btn", "n_clicks"),
    State({"type": "test-vis", "index": ALL}, "value"),
    prevent_initial_call=True,
)
def show_all_tests(n_clicks, current_values):
    if not n_clicks or not current_values:
        raise PreventUpdate
    return [["on"] for _ in range(len(current_values))]


@callback(
    Output({"type": "test-vis", "index": ALL}, "value",
           allow_duplicate=True),
    Input({"type": "card-show-all-btn", "index": ALL}, "n_clicks"),
    State({"type": "test-vis", "index": ALL}, "value"),
    State({"type": "test-vis", "index": ALL}, "id"),
    prevent_initial_call=True,
)
def card_show_all(all_clicks, current_values, vis_ids):
    if not ctx.triggered_id:
        raise PreventUpdate
    tid = ctx.triggered_id
    if not isinstance(tid, dict) or tid.get("type") != "card-show-all-btn":
        raise PreventUpdate
    if not any(c and c > 0 for c in (all_clicks or [])):
        raise PreventUpdate
    target_gi = str(tid["index"])
    new_values = list(current_values)
    for i, vid in enumerate(vis_ids):
        parts = vid.get("index", "").split("-")
        if len(parts) == 2 and parts[0] == target_gi:
            new_values[i] = ["on"]
    return new_values


# =====================================================================
# DRAWER TOGGLES — left (push) + right (overlay) + analysis (overlay)
# =====================================================================

@callback(
    Output("left-drawer", "className"),
    Input("toggle-left-drawer-btn", "n_clicks"),
    Input("comparison-results", "data"),
    State("left-drawer", "className"),
    prevent_initial_call=True,
)
def toggle_left_drawer(btn_clicks, comp_data, current_class):
    """Toggle left panel. Auto-closes when comparison results arrive."""
    triggered = ctx.triggered_id
    if triggered == "comparison-results" and comp_data:
        return "left-drawer"  # Auto-close
    if "open" in (current_class or ""):
        return "left-drawer"
    return "left-drawer open"


@callback(
    Output("right-drawer", "className"),
    Output("analysis-drawer", "className"),
    Output("ml-drawer", "className"),
    Input("toggle-drawer-btn", "n_clicks"),
    Input("toggle-analysis-drawer-btn", "n_clicks"),
    Input("toggle-ml-drawer-btn", "n_clicks"),
    State("right-drawer", "className"),
    State("analysis-drawer", "className"),
    State("ml-drawer", "className"),
    prevent_initial_call=True,
)
def toggle_right_drawers(trim_btn, analysis_btn,ml_btn,
                         trim_cls, analysis_cls, ml_cls):
    """Toggle right push-drawers. Opening one closes the other."""
    ALL_CLOSED = ("right-drawer", "right-drawer analysis", "right-drawer ml")

    triggered = ctx.triggered_id
    if not triggered:
        return ALL_CLOSED

    if triggered == "toggle-drawer-btn":
        if "open" in (trim_cls or ""):
            return ALL_CLOSED
        return ("right-drawer open", "right-drawer analysis", "right-drawer ml")

    if triggered == "toggle-analysis-drawer-btn":
        if "open" in (analysis_cls or ""):
            return ALL_CLOSED
        return ("right-drawer", "right-drawer analysis open", "right-drawer ml")

    if triggered == "toggle-ml-drawer-btn":
        if "open" in (ml_cls or ""):
            return ALL_CLOSED
        return ("right-drawer", "right-drawer analysis", "right-drawer ml open")
    return ALL_CLOSED

def _compute_residuals(curve_data):
    """Compute fitted and raw residuals for each trim group."""

    residuals = {}

    for dia, data in curve_data.items():

        result = {
            "fit": {},
            "raw": {},
        }

        # ------------------------------------------------------------------
        # Baseline data
        # ------------------------------------------------------------------

        base_head_flow = np.asarray(data.get("base_head_flow", []))
        base_head = np.asarray(data.get("base_head", []))

        base_power_flow = np.asarray(data.get("base_power_flow", []))
        base_power = np.asarray(data.get("base_power", []))

        # ------------------------------------------------------------------
        # Fitted head residuals
        # ------------------------------------------------------------------

        head_flow = np.asarray(data.get("head_flow", []))
        head = np.asarray(data.get("head", []))

        if len(head_flow) and len(base_head_flow):
            interp_head = np.interp(
                head_flow,
                base_head_flow,
                base_head,
            )

            result["fit"]["head"] = {
                "flow": head_flow.tolist(),
                "residuals": (head - interp_head).tolist(),
            }

        # ------------------------------------------------------------------
        # Fitted power residuals
        # ------------------------------------------------------------------

        power_flow = np.asarray(data.get("power_flow", []))
        power = np.asarray(data.get("power", []))

        if len(power_flow) and len(base_power_flow):
            interp_power = np.interp(
                power_flow,
                base_power_flow,
                base_power,
            )

            result["fit"]["power"] = {
                "flow": power_flow.tolist(),
                "residuals": (power - interp_power).tolist(),
            }

        # ------------------------------------------------------------------
        # Raw test residuals
        # ------------------------------------------------------------------

        for test_id, raw in data.get("raw_data", {}).items():

            raw_result = {
                "test_date": raw["test_date"],
            }

            # Head residuals
            raw_head_flow = np.asarray(raw["flow"])
            raw_head = np.asarray(raw["head"])

            if len(raw_head_flow) and len(base_head_flow):
                interp_head = np.interp(
                    raw_head_flow,
                    base_head_flow,
                    base_head,
                )
                raw_head_residuals = (raw_head - interp_head).tolist()
                raw_result["head"] = {
                    "mean_residual": float(np.mean(raw_head_residuals)),
                    "std_residual": float(np.std(raw_head_residuals)),
                    "max_residual": float(np.max(raw_head_residuals)),
                }

            # Power residuals
            raw_power_flow = np.asarray(raw["flow"])
            raw_power = np.asarray(raw["power"])

            if len(raw_power_flow) and len(base_power_flow):
                interp_power = np.interp(
                    raw_power_flow,
                    base_power_flow,
                    base_power,
                )
                raw_power_residuals = (raw_power - interp_power).tolist()
                raw_result["power"] = {
                    "mean_residual": float(np.mean(raw_power_residuals)),
                    "std_residual": float(np.std(raw_power_residuals)),
                    "max_residual": float(np.max(raw_power_residuals)),
                }

            result["raw"][test_id] = raw_result
        residuals[dia] = result

    return residuals

def get_residuals_shift(residuals, dia, chart_type):
    trend = []

    for test in residuals[dia]["raw"].values():
        trend.append({
            "date": test["test_date"],
            "mean_residual": test[chart_type]["mean_residual"]
            })
    
    return sorted(trend, key=lambda x: x["date"])

def get_residual_kpis(residuals, dia, chart_type):

    trend = get_residuals_shift(
        residuals,
        dia,
        chart_type,
    )

    if not trend:
        return {}

    values = np.array(
        [x["mean_residual"] for x in trend]
    )

    return {
        "current_shift": float(values[-1]),
        "worst_shift": float(values.min()),
        "average_shift": float(values.mean()),
        "n_tests": len(values),
    }

def forecast_threshold_date(
    residuals,
    dia,
    chart_type,
    threshold=-3.0,
):

    trend = get_residuals_shift(
        residuals,
        dia,
        chart_type,
    )

    if len(trend) < 2:
        return None

    dates = [
        datetime.strptime(
            x["date"],
            "%Y-%m-%d",
        )
        for x in trend
    ]

    values = np.array(
        [x["mean_residual"] for x in trend]
    )

    start_date = dates[0]

    days = np.array(
        [
            (d - start_date).days
            for d in dates
        ]
    )

    slope, intercept = np.polyfit(
        days,
        values,
        1,
    )

    if slope == 0:
        return None

    crossing_day = (
        threshold - intercept
    ) / slope

    crossing_date = (
        start_date
        + timedelta(days=float(crossing_day))
    )

    return {
        "threshold": threshold,
        "slope": float(slope),
        "predicted_date": crossing_date.date(),
        "current_shift": float(values[-1]),
        "already_exceeded": values[-1] <= threshold,
    }

def classify_failure_modes(
    q,
    head_residuals,
    power_residuals,
    bep_flow,
):
    """
    Classify likely hydraulic failure modes from residual curves.

    Parameters
    ----------
    q : array-like
        Flow values corresponding to residuals.
    head_residuals : array-like
    power_residuals : array-like
    bep_flow : float
    """
    if bep_flow is None:
        return {
            "mode": "Unknown",
            "confidence": 0.0,
            "reason": "Missing BEP flow",
        }
    
    q = np.asarray(q)
    head_residuals = np.asarray(head_residuals)
    power_residuals = np.asarray(power_residuals)
    # ------------------------------------------------------------------
    # Overall health metrics
    # ------------------------------------------------------------------

    head_rms = float(
        np.sqrt(np.mean(head_residuals**2))
    )

    power_rms = float(
        np.sqrt(np.mean(power_residuals**2))
    )

    head_mean_abs = float(
        np.mean(np.abs(head_residuals))
    )

    power_mean_abs = float(
        np.mean(np.abs(power_residuals))
    )

    # ------------------------------------------------------------------
    # Normal operation check
    # ------------------------------------------------------------------

    if (
        head_rms < 3.0
        and power_rms < 2.0
        and head_mean_abs < 2.0
        and power_mean_abs < 1.5
    ):
        return {
            "mode": "Normal",
            "confidence": 0.95,
            "scores": {
                "normal": 0.95,
            },
            "summary": (
                "Residuals are within expected model "
                "uncertainty and no dominant failure "
                "pattern is present."
            ),
        }
    low_mask = q < 0.8 * bep_flow
    bep_mask = (q >= 0.95 * bep_flow) & (q <= 1.05 * bep_flow)
    high_mask = q > 1.1 * bep_flow

    mean_head = float(np.mean(head_residuals))
    std_head = float(np.std(head_residuals))

    low_head = (
        float(np.mean(head_residuals[low_mask]))
        if np.any(low_mask)
        else mean_head
    )

    bep_head = (
        float(np.mean(head_residuals[bep_mask]))
        if np.any(bep_mask)
        else mean_head
    )

    high_head = (
        float(np.mean(head_residuals[high_mask]))
        if np.any(high_mask)
        else mean_head
    )

    mean_power = float(np.mean(power_residuals))

    high_power = (
        float(np.mean(power_residuals[high_mask]))
        if np.any(high_mask)
        else mean_power
    )

    scores = {}

    # --------------------------------------------------------
    # Uniform Hydraulic Wear
    # --------------------------------------------------------
    if mean_head < -1:

        flatness = max(0, 1 - std_head / 2)

        scores["uniform_hydraulic_wear"] = (
            abs(mean_head) * flatness
        )

    # --------------------------------------------------------
    # Internal Recirculation
    # --------------------------------------------------------
    if low_head < high_head:

        scores["internal_recirculation"] = (
            abs(low_head - high_head)
        )

    # --------------------------------------------------------
    # Suction Recirculation
    # --------------------------------------------------------
    if low_head < bep_head:

        scores["suction_recirculation"] = (
            abs(low_head - bep_head)
        )

    # --------------------------------------------------------
    # Cavitation Damage
    # --------------------------------------------------------
    if high_head < low_head:

        scores["cavitation_damage"] = (
            abs(high_head - low_head)
        )

    # --------------------------------------------------------
    # Roughness / Fouling
    # --------------------------------------------------------
    if mean_head < -1 and mean_power > 1:

        scores["roughness_fouling"] = (
            abs(mean_head) + mean_power
        )

    # --------------------------------------------------------
    # Passage Blockage
    # --------------------------------------------------------
    if high_head < -2 and high_power < -2:

        scores["passage_blockage"] = (
            abs(high_head) + abs(high_power)
        )

    if not scores:

        return {
            "mode": "normal",
            "confidence": 0.0,
            "scores": {},
        }

    ranked = sorted(
        scores.items(),
        key=lambda x: x[1],
        reverse=True,
    )

    best_mode, best_score = ranked[0]

    second_score = (
        ranked[1][1]
        if len(ranked) > 1
        else 0
    )

    confidence = (
        best_score / (best_score + second_score)
        if best_score > 0
        else 0
    )

    return {
        "mode": best_mode,
        "confidence": round(confidence, 3),
        "scores": dict(ranked),
        "features": {
            "mean_head": mean_head,
            "std_head": std_head,
            "low_head": low_head,
            "bep_head": bep_head,
            "high_head": high_head,
            "mean_power": mean_power,
            "high_power": high_power,
        },
    }