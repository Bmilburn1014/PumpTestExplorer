# manual_mode/callbacks.py

import threading
from dash import Input, Output, State, html, no_update
from manual_mode.layout import manual_id
from data.log_buffer import log as _log, set_progress

_idx_lock = threading.Lock()
_idx_state = {"running": False, "done": False,
              "pump_type": None, "error": None}

_lock = threading.Lock()
_mm_state = {"running": False, "done": False,
             "result": None, "error": None}

def _mm_reset():
    with _lock:
        _mm_state.update(running=False, done=False,
                         result=None, error=None)

def check_manual_result():
    with _lock:
        if not _mm_state["done"]:
            return None
        result = _mm_state["result"]
        error = _mm_state["error"]
    _mm_reset()

    if error:
        return {"result": {"error": True},
                "status": f"✗ Error: {error}",
                "summary": [html.Div(f"✗ {error}",
                            style={"color": "#d93025"})]}

    n_bl = result.get("baseline_test_count", 0)
    n_raw = result.get("total_with_data", 0)
    px_data = result.pop("px_data", None)

    summary = [
        html.Div(className="row gap-8", children=[
            html.Span("MANUAL MODE",
                      style={"fontWeight": "700", "color": "#1a73e8",
                             "fontSize": "11px",
                             "letterSpacing": "0.5px"})]),
        html.Div(className="row gap-8", style={"marginTop": "6px"},
                 children=[
            html.Span(f"Baseline: {n_bl} tests"),
            html.Span("·", style={"color": "#999"}),
            html.Span(f"Raw: {n_raw} tests with data"),
        ]),
    ]
    if result.get("baseline_trim"):
        summary.append(html.Div(
            f'Reference: trim={result["baseline_trim"]}", '
            f'speed={result.get("baseline_speed", "—")} RPM',
            style={"fontSize": "11px", "color": "#555",
                   "marginTop": "2px"}))
    for w in result.get("warnings", []):
        summary.append(html.Div(
            f"⚠ {w}", style={"color": "#d93025", "fontSize": "11px"}))

    return {"result": result,
            "status": f"✓ Done — {n_bl} baseline + {n_raw} raw",
            "summary": summary,"px_data": px_data}


def register_manual_callbacks(app):

    # 1. TOGGLE ────────────────────────────────────────────────

    @app.callback(
        Output(manual_id("panel"), "style"),
        Output("auto-mode-controls", "style"),
        Input(manual_id("toggle"), "value"),
        prevent_initial_call=True,
    )
    def toggle_manual(val):
        on = "on" in (val or [])
        if on:
            return ({"display": "block"},
                    {"opacity": "0.3", "pointerEvents": "none",
                     "transition": "opacity 0.2s"})
        return ({"display": "none"},
                {"opacity": "1", "pointerEvents": "auto",
                 "transition": "opacity 0.2s"})

    # 2. PUMP TYPE → threaded index load + show/hide part num ──

    @app.callback(
        Output(manual_id("bl-model"), "options", allow_duplicate=True),
        Output(manual_id("bl-model"), "value", allow_duplicate=True),
        Output(manual_id("bl-model"), "disabled", allow_duplicate=True),
        Output(manual_id("bl-model"), "placeholder",
               allow_duplicate=True),
        Output(manual_id("index-status"), "children"),
        Output(manual_id("index-status"), "style"),
        Output(manual_id("part-num-row"), "style"),
        Input(manual_id("pump-type"), "value"),
        prevent_initial_call=True,
    )
    def on_pump_type(pt):
        part_style = ({"display": "block", "marginBottom": "6px"}
                      if pt == "vertical"
                      else {"display": "none"})
        if not pt:
            return ([], None, True, "Select pump type first…",
                    "", {"display": "none"}, part_style)

        with _idx_lock:
            _idx_state.update(running=True, done=False,
                              pump_type=pt, error=None)
        threading.Thread(target=_load_index_thread,
                         args=(pt,), daemon=True).start()

        labels = {"inline": "Inline", "horizontal": "Horizontal",
                  "end_suction": "End Suction",
                  "vertical": "Vertical Turbine"}
        return ([], None, True, "Loading index…",
                f"⏳ Loading {labels.get(pt, pt)} index…",
                {"display": "block", "color": "#1a73e8",
                 "fontWeight": "600"},
                part_style)

    # 2b. INDEX POLLER ────────────────────────────────────────

    @app.callback(
        Output(manual_id("index-ready"), "data"),
        Input("status-interval", "n_intervals"),
        prevent_initial_call=True,
    )
    def poll_index(n):
        with _idx_lock:
            if not _idx_state["done"]:
                return no_update
            pt = _idx_state["pump_type"]
            err = _idx_state["error"]
            _idx_state["running"] = False
            _idx_state["done"] = False
        if err:
            return {"pump_type": pt, "ready": False, "error": err}
        return {"pump_type": pt, "ready": True, "error": None}

    # 2c. MODEL DROPDOWN — index-ready + baseline dates ───────

    @app.callback(
        Output(manual_id("bl-model"), "options"),
        Output(manual_id("bl-model"), "disabled"),
        Output(manual_id("bl-model"), "placeholder"),
        Output(manual_id("index-status"), "children",
               allow_duplicate=True),
        Output(manual_id("index-status"), "style",
               allow_duplicate=True),
        Input(manual_id("index-ready"), "data"),
        Input(manual_id("bl-date-start"), "value"),
        Input(manual_id("bl-date-end"), "value"),
        State(manual_id("pump-type"), "value"),
        prevent_initial_call=True,
    )
    def update_models(idx, ds, de, pt):
        if not idx or not idx.get("ready"):
            err = (idx or {}).get("error")
            if err:
                return ([], True, "Index load failed",
                        f"✗ {err}",
                        {"display": "block", "color": "#d93025"})
            return ([], True, "Select pump type first…",
                    no_update, no_update)
        if not pt:
            return ([], True, "Select pump type first…",
                    no_update, no_update)
        from manual_mode.engine import ManualModeEngine
        models = ManualModeEngine().get_models(pt, ds, de)
        n = len(models)
        ph = (f"Select model ({n} in date range)…" if ds or de
              else f"Select model ({n} available)…")
        return (models, False, ph,
                f"✓ Index loaded — {n} models",
                {"display": "block", "color": "#188038",
                 "fontWeight": "600"})

    # 3. RUN COMPARISON ───────────────────────────────────────

    @app.callback(
        Output(manual_id("compare-btn"), "disabled"),
        Output(manual_id("compare-btn"), "children"),
        Output(manual_id("status"), "children"),
        Input(manual_id("compare-btn"), "n_clicks"),
        State(manual_id("pump-type"), "value"),
        State(manual_id("bl-model"), "value"),
        State(manual_id("bl-date-start"), "value"),
        State(manual_id("bl-date-end"), "value"),
        State(manual_id("bl-trim"), "value"),
        State(manual_id("bl-speed"), "value"),
        State(manual_id("bl-part-num"), "value"),
        State(manual_id("raw-date-start"), "value"),
        State(manual_id("raw-date-end"), "value"),
        State(manual_id("trim-tolerance"), "value"),
        State("head-source", "value"),
        State("power-source", "value"),
        prevent_initial_call=True,
    )
    def start_compare(
        n_clicks, pump_type, model,
        bl_ds, bl_de, bl_trim, bl_speed, bl_part_num,
        raw_ds, raw_de,
        trim_tol, head_col, power_col,
    ):
        if not n_clicks:
            return no_update, no_update, no_update
        if not pump_type:
            return False, "Run Manual Compare", "⚠ Select a pump type."
        if not model:
            return False, "Run Manual Compare", "⚠ Select a model."

        with _lock:
            if _mm_state["running"]:
                return True, "Running…", "Already running."
            _mm_state.update(running=True, done=False,
                             result=None, error=None)

        threading.Thread(
            target=_run_manual_thread,
            args=(pump_type, model,
                  bl_ds, bl_de, bl_trim, bl_speed, bl_part_num,
                  raw_ds, raw_de, trim_tol or 3.0,
                  head_col or "tdh_bowl",
                  power_col or "power_bowl_dyno"),
            daemon=True).start()

        return True, "Running…", "Manual comparison started…"

    # 3b. RE-ENABLE BUTTON ────────────────────────────────────

    @app.callback(
        Output(manual_id("compare-btn"), "disabled",
               allow_duplicate=True),
        Output(manual_id("compare-btn"), "children",
               allow_duplicate=True),
        Input("comparison-results", "data"),
        prevent_initial_call=True,
    )
    def reenable(data):
        if data is not None:
            return False, "Run Manual Compare"
        return no_update, no_update

    # 4. READOUT ──────────────────────────────────────────────

    @app.callback(
        Output(manual_id("trim-tol-readout"), "children"),
        Input(manual_id("trim-tolerance"), "value"),
    )
    def readout(val):
        return f"±{val or 0}%"


# ── THREADS ──────────────────────────────────────────────────

def _load_index_thread(pump_type):
    try:
        _log(f"Manual: syncing index for {pump_type}…")
        from manual_mode.engine import ManualModeEngine
        eng = ManualModeEngine()
        eng.data_source.sync.sync_index_files()
        _log("Manual: index synced")
        with _idx_lock:
            _idx_state["done"] = True
    except Exception as e:
        _log(f"Manual: index load failed: {e}")
        with _idx_lock:
            _idx_state["error"] = str(e)
            _idx_state["done"] = True


def _run_manual_thread(pump_type, model,
                       bl_ds, bl_de, bl_trim, bl_speed, bl_part_num,
                       raw_ds, raw_de,
                       trim_tol, head_col, power_col):
    try:
        from manual_mode.engine import ManualModeEngine
        eng = ManualModeEngine()
        result = eng.run_manual_comparison(
            pump_type=pump_type, model=model,
            bl_date_start=bl_ds, bl_date_end=bl_de,
            bl_trim=bl_trim, bl_speed=bl_speed,
            bl_part_num=bl_part_num,
            raw_date_start=raw_ds, raw_date_end=raw_de,
            trim_tolerance_pct=trim_tol,
            head_col=head_col, power_col=power_col,
            progress_callback=_progress)
        with _lock:
            _mm_state["result"] = result
            _mm_state["done"] = True
    except Exception as e:
        import traceback
        _log(f"Manual comparison error: {e}")
        traceback.print_exc()
        with _lock:
            _mm_state["error"] = str(e)
            _mm_state["done"] = True
    set_progress(0, "")


def _progress(msg, pct):
    set_progress(pct, msg)
    _log(msg)
