# callbacks/trim_callbacks.py
#
# Handles the Trim Manager panel in the right sidebar:
#   - Building trim panels dynamically from diameter inputs
#   - Per-trim % shift and absolute shift
#   - Active/inactive toggling
#   - Layer visibility toggles
#   - Color picker changes
#   - Polynomial order changes
#   - Reset all

from dash import (
    Input, Output, State, callback, no_update, ctx,
    html, dcc, ALL,
)

from layout.main_layout import (
    MAX_TRIMS, TRIM_COLORS, LAYER_LABELS, build_trim_panel,
)


# ─────────────────────────────────────────────────────────────
# 1. BUILD TRIM PANELS when diameters change
# ─────────────────────────────────────────────────────────────

@callback(
    Output("trim-manager-panels", "children"),
    Output("trim-config", "data"),
    Input({"type": "trim-diameter", "index": ALL}, "value"),
    State("trim-config", "data"),
)
def build_trim_panels(diameters, prev_config):
    """Rebuild the trim manager panels whenever diameter inputs change."""
    prev_config = prev_config or {}
    new_config = {}
    panels = []

    for i, dia in enumerate(diameters):
        if dia is None or dia == "":
            continue

        key = f"trim_{i}"
        # Carry forward previous config or create defaults
        existing = prev_config.get(key, {})
        config = {
            "diameter": float(dia),
            "color": existing.get("color",
                                  TRIM_COLORS[i % len(TRIM_COLORS)]),
            "active": existing.get("active", True),
            "pct_shift": existing.get("pct_shift", 0.0),
            "abs_shift": existing.get("abs_shift", 0.0),
            "poly_order": existing.get("poly_order", 3),
            "layers": existing.get(
                "layers", {k: True for k in LAYER_LABELS}
            ),
            "r_squared_tdh": existing.get("r_squared_tdh", None),
            "r_squared_power": existing.get("r_squared_power", None),
            "dip": existing.get("dip", {
                "enabled": False,
                "severity": 5.0,
                "location": 40.0,
                "width": 15.0,
            }),
            "droop": existing.get("droop", {
                "enabled": False,
                "severity": 3.0,
                "onset": 25.0,
            }),
        }
        new_config[key] = config

        panel = build_trim_panel(i, float(dia), config)
        if panel:
            panels.append(panel)

    if not panels:
        panels = [html.P(
            "Enter trim diameters on the left to begin.",
            style={"color": "#999", "fontSize": "12px", "fontStyle": "italic"},
        )]

    return panels, new_config


# ─────────────────────────────────────────────────────────────
# 2. HANDLE PERCENT SHIFT (UP / DOWN / RESET)
# ─────────────────────────────────────────────────────────────

@callback(
    Output("trim-config", "data", allow_duplicate=True),
    Input({"type": "pct-shift-up", "index": ALL}, "n_clicks"),
    Input({"type": "pct-shift-down", "index": ALL}, "n_clicks"),
    Input("reset-all-btn", "n_clicks"),
    State("trim-config", "data"),
    State("shift-step", "value"),
    prevent_initial_call=True,
)
def handle_pct_shift(up_clicks, down_clicks, reset_clicks, config, step):
    triggered = ctx.triggered_id
    if not triggered or not config:
        return no_update

    # Reset all
    if triggered == "reset-all-btn":
        for key in config:
            config[key]["pct_shift"] = 0.0
            config[key]["abs_shift"] = 0.0
            config[key].get("dip", {})["severity"] = 5.0
            config[key].get("dip", {})["enabled"] = False
            config[key].get("droop", {})["severity"] = 3.0
            config[key].get("droop", {})["enabled"] = False
        return config

    if isinstance(triggered, dict):
        idx = triggered["index"]
        key = f"trim_{idx}"
        if key not in config:
            return no_update

        step = step or 1
        current = config[key].get("pct_shift", 0.0)

        if triggered["type"] == "pct-shift-up":
            config[key]["pct_shift"] = current + step
        elif triggered["type"] == "pct-shift-down":
            config[key]["pct_shift"] = current - step

    return config


# ─────────────────────────────────────────────────────────────
# 3. HANDLE ABSOLUTE SHIFT (UP / DOWN)
# ─────────────────────────────────────────────────────────────

@callback(
    Output("trim-config", "data", allow_duplicate=True),
    Input({"type": "abs-shift-up", "index": ALL}, "n_clicks"),
    Input({"type": "abs-shift-down", "index": ALL}, "n_clicks"),
    State("trim-config", "data"),
    State("abs-shift-step", "value"),
    prevent_initial_call=True,
)
def handle_abs_shift(up_clicks, down_clicks, config, step):
    triggered = ctx.triggered_id
    if not triggered or not config or not isinstance(triggered, dict):
        return no_update

    idx = triggered["index"]
    key = f"trim_{idx}"
    if key not in config:
        return no_update

    step = step or 1
    current = config[key].get("abs_shift", 0.0)

    if triggered["type"] == "abs-shift-up":
        config[key]["abs_shift"] = current + step
    elif triggered["type"] == "abs-shift-down":
        config[key]["abs_shift"] = current - step

    return config


# ─────────────────────────────────────────────────────────────
# 4. HANDLE ACTIVE TOGGLE
# ─────────────────────────────────────────────────────────────

@callback(
    Output("trim-config", "data", allow_duplicate=True),
    Input({"type": "trim-active", "index": ALL}, "value"),
    State({"type": "trim-active", "index": ALL}, "id"),
    State("trim-config", "data"),
    prevent_initial_call=True,
)
def handle_active_toggle(values, ids, config):
    if not config:
        return no_update

    for id_dict, val in zip(ids, values):
        key = f"trim_{id_dict['index']}"
        if key in config:
            config[key]["active"] = bool(val and "on" in val)

    return config


# ─────────────────────────────────────────────────────────────
# 5. HANDLE COLOR PICKER
# ─────────────────────────────────────────────────────────────

@callback(
    Output("trim-config", "data", allow_duplicate=True),
    Input({"type": "trim-color", "index": ALL}, "value"),
    State({"type": "trim-color", "index": ALL}, "id"),
    State("trim-config", "data"),
    prevent_initial_call=True,
)
def handle_color_change(colors, ids, config):
    if not config:
        return no_update

    for id_dict, color in zip(ids, colors):
        if color:
            key = f"trim_{id_dict['index']}"
            if key in config:
                config[key]["color"] = color

    return config


# ─────────────────────────────────────────────────────────────
# 6. HANDLE LAYER TOGGLES
# ─────────────────────────────────────────────────────────────

@callback(
    Output("trim-config", "data", allow_duplicate=True),
    Input({"type": "layer-raw", "index": ALL}, "value"),
    Input({"type": "layer-corrected", "index": ALL}, "value"),
    Input({"type": "layer-polyfit", "index": ALL}, "value"),
    Input({"type": "layer-band_6pct", "index": ALL}, "value"),
    Input({"type": "layer-dip", "index": ALL}, "value"),
    Input({"type": "layer-droop", "index": ALL}, "value"),
    State({"type": "layer-raw", "index": ALL}, "id"),
    State("trim-config", "data"),
    prevent_initial_call=True,
)
def handle_layer_toggles(raw_vals, corr_vals, poly_vals, band_vals,
                         dip_vis_vals, droop_vis_vals, ids, config):
    if not config:
        return no_update

    layer_keys = ["raw", "corrected", "polyfit", "band_6pct",
                  "dip", "droop"]
    all_vals = [raw_vals, corr_vals, poly_vals, band_vals,
                dip_vis_vals, droop_vis_vals]

    for id_dict, *layer_values in zip(ids, *all_vals):
        key = f"trim_{id_dict['index']}"
        if key in config:
            for lk, lv in zip(layer_keys, layer_values):
                config[key].setdefault("layers", {})[lk] = bool(
                    lv and "on" in lv
                )

    return config


# ─────────────────────────────────────────────────────────────
# 7. HANDLE POLYNOMIAL ORDER CHANGE
# ─────────────────────────────────────────────────────────────

@callback(
    Output("trim-config", "data", allow_duplicate=True),
    Input({"type": "poly-order", "index": ALL}, "value"),
    State({"type": "poly-order", "index": ALL}, "id"),
    State("trim-config", "data"),
    prevent_initial_call=True,
)
def handle_poly_order_change(orders, ids, config):
    if not config:
        return no_update

    for id_dict, order in zip(ids, orders):
        if order is not None:
            key = f"trim_{id_dict['index']}"
            if key in config:
                config[key]["poly_order"] = int(order)

    return config


# ─────────────────────────────────────────────────────────────
# 8. HANDLE DIP CONTROLS
# ─────────────────────────────────────────────────────────────

@callback(
    Output("trim-config", "data", allow_duplicate=True),
    Input({"type": "dip-enabled", "index": ALL}, "value"),
    Input({"type": "dip-severity", "index": ALL}, "value"),
    Input({"type": "dip-location", "index": ALL}, "value"),
    Input({"type": "dip-width", "index": ALL}, "value"),
    State({"type": "dip-enabled", "index": ALL}, "id"),
    State("trim-config", "data"),
    prevent_initial_call=True,
)
def handle_dip_controls(enabled_vals, severity_vals, location_vals,
                        width_vals, ids, config):
    if not config:
        return no_update

    for id_dict, en, sev, loc, wid in zip(
        ids, enabled_vals, severity_vals, location_vals, width_vals
    ):
        key = f"trim_{id_dict['index']}"
        if key in config:
            config[key].setdefault("dip", {})
            config[key]["dip"]["enabled"] = bool(en and "on" in en)
            if sev is not None:
                config[key]["dip"]["severity"] = float(sev)
            if loc is not None:
                config[key]["dip"]["location"] = float(loc)
            if wid is not None:
                config[key]["dip"]["width"] = float(wid)

    return config


# ─────────────────────────────────────────────────────────────
# 9. HANDLE DROOP CONTROLS
# ─────────────────────────────────────────────────────────────

@callback(
    Output("trim-config", "data", allow_duplicate=True),
    Input({"type": "droop-enabled", "index": ALL}, "value"),
    Input({"type": "droop-severity", "index": ALL}, "value"),
    Input({"type": "droop-onset", "index": ALL}, "value"),
    State({"type": "droop-enabled", "index": ALL}, "id"),
    State("trim-config", "data"),
    prevent_initial_call=True,
)
def handle_droop_controls(enabled_vals, severity_vals, onset_vals,
                          ids, config):
    if not config:
        return no_update

    for id_dict, en, sev, onset in zip(
        ids, enabled_vals, severity_vals, onset_vals
    ):
        key = f"trim_{id_dict['index']}"
        if key in config:
            config[key].setdefault("droop", {})
            config[key]["droop"]["enabled"] = bool(en and "on" in en)
            if sev is not None:
                config[key]["droop"]["severity"] = float(sev)
            if onset is not None:
                config[key]["droop"]["onset"] = float(onset)