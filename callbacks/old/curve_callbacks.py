# callbacks/curve_callbacks.py
#
# Handles:
#   - Dynamic curve manager list (checkboxes + shift arrows per curve)
#   - Curve offset shifting (up/down by percentage)
#   - Active/inactive toggling (opacity control)
#   - Crosshair readout on hover
#   - Chart rendering with offsets and opacity applied

from dash import (
    Input, Output, State, callback, no_update, ctx,
    html, dcc, ALL, MATCH,
)
import plotly.graph_objects as go
import pandas as pd
import json


# ─────────────────────────────────────────────────────────────
# 1. BUILD THE CURVE MANAGER LIST
#    Fires when the user selects datasets + Y columns.
#    Creates one row per curve with checkbox, label, ▲, ▼, offset.
# ─────────────────────────────────────────────────────────────

@callback(
    Output("curve-manager-list", "children"),
    Output("active-curves", "data"),
    Output("curve-offsets", "data"),
    Input("dataset-selector", "value"),
    Input("y-axis-selector", "value"),
    State("active-curves", "data"),
    State("curve-offsets", "data"),
)
def build_curve_manager(datasets, y_cols, prev_active, prev_offsets):
    if not datasets or not y_cols:
        return [], [], {}

    # Build list of curve keys: "dataset|column"
    curve_keys = []
    for ds in datasets:
        for col in y_cols:
            curve_keys.append(f"{ds}|{col}")

    # Preserve existing state for curves that still exist
    active = [k for k in curve_keys if k in (prev_active or curve_keys)]
    # Default new curves to active
    for k in curve_keys:
        if k not in (prev_active or []) and k not in active:
            active.append(k)

    offsets = {}
    for k in curve_keys:
        offsets[k] = (prev_offsets or {}).get(k, 0.0)

    # Build UI rows
    rows = []
    for key in curve_keys:
        ds_name, col_name = key.split("|", 1)
        label = f"{ds_name} — {col_name}"
        offset_val = offsets.get(key, 0.0)
        is_active = key in active

        row = html.Div(
            style={
                "display": "flex",
                "alignItems": "center",
                "padding": "6px 4px",
                "borderBottom": "1px solid #eee",
                "gap": "6px",
                "opacity": "1" if is_active else "0.5",
            },
            children=[
                # Active checkbox
                dcc.Checklist(
                    id={"type": "curve-active", "key": key},
                    options=[{"label": "", "value": "on"}],
                    value=["on"] if is_active else [],
                    style={"minWidth": "20px"},
                ),

                # Curve label + color swatch (color assigned in chart)
                html.Span(
                    label,
                    title=label,
                    style={
                        "flex": "1",
                        "fontSize": "12px",
                        "overflow": "hidden",
                        "textOverflow": "ellipsis",
                        "whiteSpace": "nowrap",
                    },
                ),

                # Shift down button
                html.Button(
                    "▼",
                    id={"type": "shift-down", "key": key},
                    n_clicks=0,
                    style={
                        "width": "28px",
                        "height": "28px",
                        "padding": "0",
                        "fontSize": "14px",
                        "cursor": "pointer",
                        "border": "1px solid #ccc",
                        "borderRadius": "3px",
                        "backgroundColor": "#fff",
                    },
                ),

                # Shift up button
                html.Button(
                    "▲",
                    id={"type": "shift-up", "key": key},
                    n_clicks=0,
                    style={
                        "width": "28px",
                        "height": "28px",
                        "padding": "0",
                        "fontSize": "14px",
                        "cursor": "pointer",
                        "border": "1px solid #ccc",
                        "borderRadius": "3px",
                        "backgroundColor": "#fff",
                    },
                ),

                # Offset readout
                html.Span(
                    f"{offset_val:+.1f}%",
                    id={"type": "offset-display", "key": key},
                    style={
                        "width": "55px",
                        "textAlign": "right",
                        "fontSize": "12px",
                        "fontFamily": "monospace",
                        "color": "#2196F3" if offset_val != 0 else "#999",
                    },
                ),
            ],
        )
        rows.append(row)

    return rows, active, offsets


# ─────────────────────────────────────────────────────────────
# 2. HANDLE SHIFT BUTTONS (UP / DOWN)
#    Uses pattern-matching callbacks to handle any number of curves.
# ─────────────────────────────────────────────────────────────

@callback(
    Output("curve-offsets", "data", allow_duplicate=True),
    Input({"type": "shift-up", "key": ALL}, "n_clicks"),
    Input({"type": "shift-down", "key": ALL}, "n_clicks"),
    Input("reset-offsets-btn", "n_clicks"),
    State("curve-offsets", "data"),
    State("shift-step", "value"),
    prevent_initial_call=True,
)
def handle_shift(up_clicks, down_clicks, reset_clicks, offsets, step):
    triggered = ctx.triggered_id

    if not triggered:
        return no_update

    # Reset all offsets
    if triggered == "reset-offsets-btn":
        return {k: 0.0 for k in (offsets or {})}

    # Identify which curve and direction
    if isinstance(triggered, dict):
        key = triggered["key"]
        direction = triggered["type"]

        if offsets is None:
            offsets = {}

        current = offsets.get(key, 0.0)
        step = step or 5

        if direction == "shift-up":
            offsets[key] = current + step
        elif direction == "shift-down":
            offsets[key] = current - step

    return offsets


# ─────────────────────────────────────────────────────────────
# 3. HANDLE ACTIVE/INACTIVE TOGGLE
#    Updates the active-curves store when a checkbox changes.
# ─────────────────────────────────────────────────────────────

@callback(
    Output("active-curves", "data", allow_duplicate=True),
    Input({"type": "curve-active", "key": ALL}, "value"),
    State({"type": "curve-active", "key": ALL}, "id"),
    prevent_initial_call=True,
)
def handle_active_toggle(values, ids):
    active = []
    for id_dict, val in zip(ids, values):
        if val and "on" in val:
            active.append(id_dict["key"])
    return active


# ─────────────────────────────────────────────────────────────
# 4. RENDER THE CHART
#    Reads from the data source, applies offsets, and sets opacity
#    based on active/inactive state.
# ─────────────────────────────────────────────────────────────

# A stable color palette so curves keep their colors across updates
CURVE_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#393b79", "#637939", "#8c6d31", "#843c39", "#7b4173",
    "#5254a3", "#6b6ecf", "#9c9ede", "#ad494a", "#a55194",
]

ACTIVE_OPACITY = 1.0
INACTIVE_OPACITY = 0.15


@callback(
    Output("main-chart", "figure"),
    Input("dataset-selector", "value"),
    Input("x-axis-selector", "value"),
    Input("y-axis-selector", "value"),
    Input("chart-type", "value"),
    Input("value-range", "value"),
    Input("date-range", "start_date"),
    Input("date-range", "end_date"),
    Input("curve-offsets", "data"),
    Input("active-curves", "data"),
)
def update_chart(
    datasets, x_col, y_cols, chart_type,
    value_range, start_date, end_date,
    offsets, active_curves,
):
    if not datasets or not x_col or not y_cols:
        # Return empty chart with instructions
        fig = go.Figure()
        fig.update_layout(
            template="plotly_white",
            annotations=[{
                "text": "Select datasets and columns to begin",
                "xref": "paper", "yref": "paper",
                "x": 0.5, "y": 0.5,
                "showarrow": False,
                "font": {"size": 18, "color": "#999"},
            }],
        )
        return fig

    from data.loader import DataSource
    ds = DataSource()

    offsets = offsets or {}
    active_curves = active_curves or []

    fig = go.Figure()
    color_idx = 0

    for dataset_key in datasets:
        df = ds.get_detail_data(dataset_key)

        # Apply filters
        if value_range and "value" in df.columns:
            df = df[
                (df["value"] >= value_range[0])
                & (df["value"] <= value_range[1])
            ]
        if start_date and end_date and "date" in df.columns:
            df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]

        if df.empty or x_col not in df.columns:
            continue

        for y_col in y_cols:
            if y_col not in df.columns:
                continue

            curve_key = f"{dataset_key}|{y_col}"
            color = CURVE_COLORS[color_idx % len(CURVE_COLORS)]
            color_idx += 1

            is_active = curve_key in active_curves
            opacity = ACTIVE_OPACITY if is_active else INACTIVE_OPACITY

            # Apply percentage offset
            offset_pct = offsets.get(curve_key, 0.0)
            y_data = df[y_col].copy()
            if offset_pct != 0 and pd.api.types.is_numeric_dtype(y_data):
                y_data = y_data * (1 + offset_pct / 100.0)

            # Build curve name with offset indicator
            name = f"{dataset_key} — {y_col}"
            if offset_pct != 0:
                name += f" ({offset_pct:+.1f}%)"

            # Choose trace type
            common = dict(
                x=df[x_col],
                y=y_data,
                name=name,
                opacity=opacity,
                legendgroup=curve_key,
                showlegend=True,
                hovertemplate=(
                    f"<b>{name}</b><br>"
                    f"{x_col}: %{{x}}<br>"
                    f"{y_col}: %{{y:.4f}}<br>"
                    f"<extra></extra>"
                ),
            )

            if chart_type == "Scatter":
                fig.add_trace(go.Scatter(
                    mode="markers",
                    marker=dict(color=color, size=5),
                    **common,
                ))
            elif chart_type == "Area":
                fig.add_trace(go.Scatter(
                    mode="lines",
                    fill="tozeroy",
                    line=dict(color=color, width=2),
                    fillcolor=_rgba(color, opacity * 0.3),
                    **common,
                ))
            else:  # Line (default)
                fig.add_trace(go.Scatter(
                    mode="lines",
                    line=dict(
                        color=color,
                        width=2.5 if is_active else 1.5,
                    ),
                    **common,
                ))

    # Layout
    fig.update_layout(
        template="plotly_white",
        dragmode="zoom",
        hovermode="x unified",    # crosshair follows X, shows all curves
        xaxis=dict(
            title=x_col,
            showspikes=True,       # vertical spike line at cursor
            spikemode="across",
            spikesnap="cursor",
            spikethickness=1,
            spikecolor="#888",
            spikedash="dot",
        ),
        yaxis=dict(
            title="Value",
            showspikes=True,       # horizontal spike line at cursor
            spikemode="across",
            spikesnap="cursor",
            spikethickness=1,
            spikecolor="#888",
            spikedash="dot",
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(size=11),
        ),
        margin=dict(l=60, r=20, t=40, b=60),
        uirevision="constant",    # preserves zoom/pan across updates
    )

    return fig


# ─────────────────────────────────────────────────────────────
# 5. CROSSHAIR READOUT
#    Updates the readout bar when hovering over the chart.
# ─────────────────────────────────────────────────────────────

@callback(
    Output("crosshair-readout", "children"),
    Input("main-chart", "hoverData"),
    State("active-curves", "data"),
)
def update_crosshair_readout(hover_data, active_curves):
    if not hover_data or "points" not in hover_data:
        return "Hover over chart for cursor readout"

    points = hover_data["points"]
    if not points:
        return "Hover over chart for cursor readout"

    # Build readout string: X value + each curve's Y value
    x_val = points[0].get("x", "")
    parts = [f"X: {x_val}"]

    for pt in points:
        curve_name = pt.get("fullData", {}).get("name", pt.get("curveNumber", ""))
        # Fallback: try the customdata or just show the y value
        y_val = pt.get("y", "")
        if isinstance(y_val, (int, float)):
            parts.append(f"{curve_name}: {y_val:.4f}")
        else:
            parts.append(f"{curve_name}: {y_val}")

    # Truncate if too many curves
    if len(parts) > 8:
        parts = parts[:8] + ["..."]

    return "  │  ".join(parts)


# ─────────────────────────────────────────────────────────────
# 6. POPULATE COLUMN DROPDOWNS
#    When datasets change, update the X and Y column options.
# ─────────────────────────────────────────────────────────────

@callback(
    Output("x-axis-selector", "options"),
    Output("y-axis-selector", "options"),
    Output("dataset-selector", "options"),
    Input("dataset-selector", "value"),
    Input("sync-index-btn", "n_clicks"),
)
def update_column_options(selected_datasets, _sync_clicks):
    from data.loader import DataSource
    ds = DataSource()

    # Always refresh the dataset list
    all_keys = ds.list_available_keys()
    dataset_options = [{"label": k, "value": k} for k in all_keys]

    if not selected_datasets:
        return [], [], dataset_options

    # Get columns from the first selected dataset to populate dropdowns
    try:
        sample_df = ds.get_detail_data(selected_datasets[0])
        columns = list(sample_df.columns)
    except Exception:
        columns = []

    col_options = [{"label": c, "value": c} for c in columns]
    return col_options, col_options, dataset_options


# ─────────────────────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────────────────────

def _rgba(hex_color: str, alpha: float) -> str:
    """Convert #rrggbb to rgba(r, g, b, a)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"
