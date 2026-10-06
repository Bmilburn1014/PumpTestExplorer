# callbacks/comparison_callbacks.py
#
# Threading approach with clean output separation:
#
#   start_comparison  → fires on button click, starts thread
#                       outputs: run-btn (disabled/children), run-status
#
#   poll_job          → fires on 500ms interval, checks thread
#                       outputs: progress bar, progress label,
#                                status-line-1/2,
#                                comparison-results (when done),
#                                results-summary, trim-group-panels,
#                                run-btn (re-enable when done)
#
# Thread writes to log_buffer (shared memory), so status bar updates.

import sys
import os
import traceback
import threading
import re
import pandas as pd
from dash import callback, Input, Output, State, html, no_update, ctx
from layout.main_layout import build_trim_group_card
from data.log_buffer import log as _log
from data.pump_classifier import classify_pump, build_model_search_pattern
from config import INDEX_FILES

_DATA_SOURCE = None

# ── Thread state ─────────────────────────────────────────────
_lock = threading.Lock()
_state = {
    "running": False,
    "done": False,
    "result": None,     # serialized dict
    "error": None,      # error string
    "status": "",       # status text
    "summary_html": [], # list of html components
    "panels_html": [],  # list of html components
}


def _reset():
    with _lock:
        _state["running"] = False
        _state["done"] = False
        _state["result"] = None
        _state["error"] = None
        _state["status"] = ""
        _state["summary_html"] = []
        _state["panels_html"] = []

# ── Helper Functions ────────────────────────────────────────────────────
def _get_data_source():
    global _DATA_SOURCE
    if _DATA_SOURCE is None:
        from data.loader import DataSource
        _DATA_SOURCE = DataSource()
    return _DATA_SOURCE

def _load_index_dataframe(index_name):
    """
    Use the same normalized index loader used by ComparisonEngine.
    """
    return _get_data_source().get_index_data(index_name)


def _find_col(df, candidates):
    if df is None or df.empty:
        return None

    normalized = {
        str(c).strip().lower().replace(" ", "_").replace(".", ""): c
        for c in df.columns
    }

    for cand in candidates:
        key = str(cand).strip().lower().replace(" ", "_").replace(".", "")
        if key in normalized:
            return normalized[key]

    return None


def _filter_model_for_dropdown(df, curve_or_model):
    if df.empty:
        return df

    if not curve_or_model:
        return df

    model_col = _find_col(df, [
        "Pump Model",
        "pump_model",
        "model",
    ])

    if model_col is None:
        return df

    pattern = build_model_search_pattern(curve_or_model)

    before = len(df)
    mask = df[model_col].astype(str).str.contains(
        pattern,
        case=False,
        na=False,
        regex=True,
    )
    out = df[mask].copy()

    # Small safety fallback for vertical indexes:
    # If the full pattern matches nothing, do not permanently hide options.
    if out.empty:
        return df

    return out


def _filter_bowl_for_dropdown(df, classification):
    if df.empty:
        return df

    if classification.pump_type not in ("vertical", "vt"):
        return df

    if classification.bowl_diameter is None:
        return df

    bowl_col = _find_col(df, [
        "Bowl Size",
        "Bowl Diameter",
    ])

    if bowl_col is None:
        return df

    before = len(df)

    bowl_vals = pd.to_numeric(
        df[bowl_col].astype(str).str.strip(),
        errors="coerce",
    )

    out = df[bowl_vals == classification.bowl_diameter].copy()


    if out.empty:
        return df

    return out


def _filter_date_range_for_dropdown(df, date_start, date_end):
    if df.empty:
        return df

    if not date_start and not date_end:
        return df

    date_col = _find_col(df, [
        "test_date",
        "date",
        "Test Date",
        "Date",
    ])

    if date_col is None:
        return df

    before = len(df)

    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")

    effective_start = date_start
    effective_end = date_end

    if date_start and date_end:
        try:
            if pd.to_datetime(date_start) > pd.to_datetime(date_end):
                effective_start, effective_end = date_end, date_start
        except Exception:
            pass

    if effective_start:
        out = out[out[date_col] >= pd.to_datetime(effective_start)]

    if effective_end:
        out = out[out[date_col] <= pd.to_datetime(effective_end)]
    return out


def _filter_test_iteration_for_dropdown(df, test_iteration):
    if df.empty:
        return df

    if test_iteration in (None, 3, "3", "All", "all"):
        return df

    iter_col = _find_col(df, [
        "Test Run",
        "Test Iteration",
    ])

    if iter_col is None:
        return df

    before = len(df)

    iter_number = (
        df[iter_col]
        .astype(str)
        .str.extract(r"(\d+)", expand=False)
    )

    out = df[iter_number == str(test_iteration)].copy()

    return out


def _extract_impeller_part_options(df):
    if df.empty:
        return []

    part_col = _find_col(df, [
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
        "impeller_no",
        "Impeller No",
        "impeller_number",
        "Impeller Number",
        "impeller p/n",
        "Impeller P/N",
        "impeller_pn",
        "top_impeller_part_number",
        "Top Impeller Part Number",
        "top_impeller_part_no",
        "Top Impeller Part No",
    ])
    if part_col is not None:
        parts = (
            df[part_col]
            .dropna()
            .astype(str)
            .str.strip()
        )

    else:
        model_col = _find_col(df, [
            "pump_model",
            "model",
            "curve",
            "curve_number",
            "Pump Model",
            "Model",
            "Curve Number",
        ])

        if model_col is None:
            return []

        parts = (
            df[model_col]
            .apply(lambda value: classify_pump(str(value)).impeller_part)
            .dropna()
            .astype(str)
            .str.strip()
        )

    unique_parts = sorted({
        part
        for part in parts
        if part and part.lower() not in ("nan", "none", "null")
    })

    return [
        {"label": part, "value": part}
        for part in unique_parts
    ]


@callback(
    Output("vertical-impeller-filter-wrap", "className"),
    Output("vertical-impeller-parts", "options"),
    Output("vertical-impeller-parts", "value"),
    Output("vertical-impeller-parts", "disabled"),
    Output("vertical-impeller-parts-help", "children"),
    Input("px-data", "data"),
    Input("date-start", "value"),
    Input("date-end", "value"),
    Input("test-iteration", "value"),
    State("vertical-impeller-parts", "value"),
)
def update_vertical_impeller_part_options(
    px_data,
    date_start,
    date_end,
    test_iteration,
    selected_parts,
):

    if not px_data:
        return (
            "vertical-impeller-filter hidden",
            [],
            [],
            True,
            "Load a PX file first.",
        )

    curve_or_model = (
        px_data.get("curve_number")
        or px_data.get("pump_model")
        or px_data.get("model")
        or px_data.get("name")
        or px_data.get("file_name")
        or ""
    )

    classification = classify_pump(curve_or_model)

    if classification.pump_type not in ("vertical", "vt"):
        return (
            "vertical-impeller-filter hidden",
            [],
            [],
            True,
            "Impeller part filtering is only available for vertical pumps.",
        )

    try:
        df = _load_index_dataframe(classification.index_name)
    except Exception as e:
        return (
            "vertical-impeller-filter",
            [],
            [],
            True,
            f"Could not load index '{classification.index_name}': {e}",
        )

    df = _filter_model_for_dropdown(df, curve_or_model)
    df = _filter_bowl_for_dropdown(df, classification)
    df = _filter_date_range_for_dropdown(df, date_start, date_end)
    df = _filter_test_iteration_for_dropdown(df, test_iteration)

    options = _extract_impeller_part_options(df)

    valid_values = {opt["value"] for opt in options}

    selected_parts = selected_parts or []
    selected_parts = [
        str(part).strip()
        for part in selected_parts
        if str(part).strip() in valid_values
    ]

    if not options:
        return (
            "vertical-impeller-filter",
            [],
            [],
            True,
            "No impeller part numbers found for the selected filters. "
        )

    return (
        "vertical-impeller-filter",
        options,
        selected_parts,
        False,
        f"{len(options)} impeller part number option(s) available. "
    )

# ═════════════════════════════════════════════════════════════
# 0. BROWSE FOR PX FILE
# ═════════════════════════════════════════════════════════════

@callback(
    Output("px-file-path", "value"),
    Input("browse-px-btn", "n_clicks"),
    prevent_initial_call=True,
)
def browse_for_px_file(n_clicks):
    if not n_clicks:
        return no_update
    try:
        import subprocess
        import platform

        if platform.system() == "Windows":
            # Use PowerShell's native file dialog — works with flaskwebgui
            # Creates a hidden topmost owner form so the dialog opens
            # in front of the app window, not behind it.
            ps_script = (
                'Add-Type -AssemblyName System.Windows.Forms; '
                '$owner = New-Object System.Windows.Forms.Form; '
                '$owner.TopMost = $true; '
                '$owner.ShowInTaskbar = $false; '
                '$owner.WindowState = "Minimized"; '
                '$owner.Show(); '
                '$owner.Hide(); '
                '$f = New-Object System.Windows.Forms.OpenFileDialog; '
                '$f.Title = "Select PX File"; '
                '$f.Filter = "Excel Files (*.xlsx;*.xlsm)|*.xlsx;*.xlsm'
                '|All Files (*.*)|*.*"; '
                '$null = $f.ShowDialog($owner); '
                'Write-Output $f.FileName; '
                '$owner.Dispose()'
            )
            # CREATE_NO_WINDOW (0x08000000) hides the PowerShell
            # console that otherwise flashes when running as .exe
            CREATE_NO_WINDOW = 0x08000000
            result = subprocess.run(
                ["powershell", "-WindowStyle", "Hidden",
                 "-Command", ps_script],
                capture_output=True, text=True, timeout=120,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            # Fallback: tkinter for non-Windows
            result = subprocess.run(
                [sys.executable, "-c",
                 "import tkinter as tk; "
                 "from tkinter import filedialog; "
                 "root = tk.Tk(); root.withdraw(); "
                 "root.attributes('-topmost', True); "
                 "root.lift(); root.focus_force(); "
                 "f = filedialog.askopenfilename("
                 "  parent=root, title='Select PX File', "
                 "  filetypes=[('Excel', '*.xlsx *.xlsm'), "
                 "             ('All', '*.*')]); "
                 "print(f); root.destroy()"],
                capture_output=True, text=True, timeout=120,
            )

        path = result.stdout.strip()
        if result.returncode != 0 and result.stderr:
            _log(f"Browse error: {result.stderr[:200]}")
        return path if path else no_update
    except subprocess.TimeoutExpired:
        _log("Browse dialog timed out")
        return no_update
    except Exception as e:
        _log(f"Browse failed: {e}")
        return no_update


# ═════════════════════════════════════════════════════════════
# 1. LOAD PX FILE
# ═════════════════════════════════════════════════════════════

@callback(
    Output("px-data", "data"),
    Output("px-status", "children"),
    Output("px-info-panel", "children"),
    Output("px-info-panel", "className"),
    Output("run-btn", "disabled", allow_duplicate=True),
    Input("load-px-btn", "n_clicks"),
    State("px-file-path", "value"),
    prevent_initial_call=True,
)
def load_px_file(n_clicks, path):
    if not path or not path.strip():
        return (no_update, "Enter a PX file path.",
                no_update, "info-banner hidden", True)
    # Windows "Copy as path" wraps in double quotes — strip them
    path = path.strip().strip('"').strip("'")

    # Validate file extension before attempting to read
    import os
    ext = os.path.splitext(path)[1].lower()
    supported = {".xlsx", ".xlsm", ".xltx", ".xltm"}
    if ext not in supported:
        if ext:
            msg = (f"Unsupported file type: {ext}")
            hint = (f"PX curve files must be .xlsx or .xlsm. "
                    f"The file you selected is a {ext} file.")
        else:
            msg = "No file extension"
            hint = ("The path does not have a recognized file extension. "
                    "PX curve files must be .xlsx or .xlsm.")
        return (no_update,
                f"✗ {msg}",
                html.Div([
                    html.Div(msg,
                             style={"fontWeight": "600", "color": "#d93025"}),
                    html.Div(os.path.basename(path) or path,
                             style={"fontFamily": "monospace",
                                    "fontSize": "11px", "padding": "4px 0"}),
                    html.Div(hint,
                             style={"fontSize": "11px", "color": "#555",
                                    "marginTop": "4px"}),
                ]),
                "info-banner", True)

    try:
        from data.px_curves import read_px_curves
        px = read_px_curves(path)
        rated_dia = px.diameters[0] if px.diameters else 0
        info = [
            html.Span(f"Curve: {px.curve_number}  "),
            html.Span(f"Rated: {rated_dia}\"  "),
            html.Span(f"Speed: {px.rated_speed} RPM  "),
        ]
        if px.rated_speed == 0:
            info.append(html.Span(
                "⚠ Speed missing — affinity scaling will be inaccurate. "
                "Check Curve Header Data sheet.",
                style={"color": "#d93025", "fontWeight": "600",
                       "display": "block", "marginTop": "4px"}))
        px_serial = _serialize_px(px)
        trims = len(px.trims)
        data_pts = sum(
            len(t.head_flow) for t in px.trims if t.head_flow is not None
        )
        if trims == 0:
            info.append(html.Span(
                "⚠ No trim data found — the curve data sheet appears "
                "to be empty. Verify the PX file has data rows starting "
                "at row 10 in the curve sheet.",
                style={"color": "#d93025", "fontWeight": "600",
                       "display": "block", "marginTop": "4px"}))
            status = "⚠ Loaded but empty — 0 trims found"
            return (px_serial, status, info, "info-banner", True)
        if data_pts == 0:
            info.append(html.Span(
                "⚠ Trims found but no curve data — head/power columns "
                "may be empty. Check the 21-column block layout.",
                style={"color": "#d93025", "fontWeight": "600",
                       "display": "block", "marginTop": "4px"}))
            status = f"⚠ Loaded — {trims} trims but 0 data points"
            return (px_serial, status, info, "info-banner", True)
        status = f"✓ Loaded — {trims} trims, {data_pts} data points"
        return (px_serial, status, info, "info-banner", False)
    except FileNotFoundError:
        import os
        # Check which part of the path doesn't exist
        parts = path.replace("/", os.sep).split(os.sep)
        hint = ""
        if path.startswith("\\\\") or path.startswith("//"):
            hint = (" If this is a network drive, check that you are "
                    "connected to the VPN or that the share is accessible.")
        elif len(parts) >= 2:
            drive = parts[0] + os.sep
            if not os.path.exists(drive):
                hint = f" Drive {drive} does not appear to be available."

        return (no_update,
                f"✗ File not found",
                html.Div([
                    html.Div("The file could not be found at this path:",
                             style={"fontWeight": "600", "color": "#d93025"}),
                    html.Div(path,
                             style={"fontFamily": "monospace", "fontSize": "11px",
                                    "padding": "4px 0", "wordBreak": "break-all"}),
                    html.Div(
                        f"Check that the path is correct and the file "
                        f"exists.{hint}",
                        style={"fontSize": "11px", "color": "#555",
                               "marginTop": "4px"}),
                ]),
                "info-banner", True)
    except PermissionError:
        return (no_update,
                "✗ Permission denied",
                html.Div([
                    html.Div("Cannot open this file — permission denied.",
                             style={"fontWeight": "600", "color": "#d93025"}),
                    html.Div(path,
                             style={"fontFamily": "monospace", "fontSize": "11px",
                                    "padding": "4px 0", "wordBreak": "break-all"}),
                    html.Div(
                        "The file may be open in Excel, locked by another "
                        "user, or in a protected directory.",
                        style={"fontSize": "11px", "color": "#555",
                               "marginTop": "4px"}),
                ]),
                "info-banner", True)
    except Exception as e:
        err_msg = str(e)
        # Detect openpyxl format errors (wrong file type)
        if "does not support" in err_msg and "file format" in err_msg:
            return (no_update,
                    "✗ Invalid file format",
                    html.Div([
                        html.Div("This file is not a valid Excel workbook.",
                                 style={"fontWeight": "600", "color": "#d93025"}),
                        html.Div(os.path.basename(path),
                                 style={"fontFamily": "monospace",
                                        "fontSize": "11px", "padding": "4px 0"}),
                        html.Div(
                            "Make sure you are selecting a PX curve file "
                            "(.xlsx or .xlsm). CSV, .xls (legacy), and "
                            "other formats are not supported.",
                            style={"fontSize": "11px", "color": "#555",
                                   "marginTop": "4px"}),
                    ]),
                    "info-banner", True)
        return (no_update, f"✗ Error loading file",
                html.Div([
                    html.Div("An unexpected error occurred:",
                             style={"fontWeight": "600", "color": "#d93025"}),
                    html.Div(err_msg,
                             style={"fontFamily": "monospace", "fontSize": "11px",
                                    "padding": "4px 0", "wordBreak": "break-all",
                                    "color": "#555"}),
                ]),
                "info-banner", True)


# ═════════════════════════════════════════════════════════════
# 2a. START COMPARISON — launches thread, returns immediately
# ═════════════════════════════════════════════════════════════

@callback(
    Output("run-btn", "disabled"),
    Output("run-btn", "children"),
    Output("run-status", "children"),
    Input("run-btn", "n_clicks"),
    State("px-file-path", "value"),
    State("date-start", "value"),
    State("date-end", "value"),
    State("trim-tolerance", "value"),
    State("head-source", "value"),
    State("power-source", "value"),
    State("test-iteration", "value"),
    State("vertical-impeller-parts", "value"),
    prevent_initial_call=True,
)
def start_comparison(n_clicks, px_path, date_start, date_end,
                     trim_tol, head_col, power_col, test_iter, vert_parts):
    if not px_path:
        return no_update, no_update, "⚠ Load a PX file first."

    with _lock:
        if _state["running"]:
            return no_update, no_update, "⚠ Already running…"

    _reset()
    with _lock:
        _state["running"] = True

    # Sanitize dates
    from datetime import date as dt_date
    today = str(dt_date.today())
    if date_start and date_start == today and date_end and date_end == today:
        date_start = None
        date_end = None
    if date_start == date_end and date_start:
        date_start = None
        date_end = None

    _log("Starting comparison…")

    t = threading.Thread(
        target=_comparison_thread,
        args=(px_path.strip().strip('"').strip("'"), date_start, date_end,
              trim_tol or 5.0, head_col or "tdh_bowl",
              power_col or "power_bowl_dyno",
              test_iter or 1.0, vert_parts or []),
        daemon=True,
    )
    t.start()

    return True, "Running…", "⏳ Running comparison…"


def _comparison_thread(px_path, date_start, date_end,
                       trim_tol, head_col, power_col,
                       test_iter, vert_parts):
    """Runs in background thread. Writes to log_buffer and _state."""
    from data.log_buffer import set_progress
    try:
        from data.comparison_engine import ComparisonEngine
        engine = ComparisonEngine()

        def prog(msg, pct):
            set_progress(pct, msg)

        result = engine.run_comparison(
            px_file_path=px_path,
            date_start=date_start,
            date_end=date_end,
            trim_tolerance_pct=trim_tol,
            head_col=head_col,
            power_col=power_col,
            progress_callback=prog,
            test_iteration=test_iter,
            impeller_part_numbers=vert_parts
        )

        results_serial = _serialize_results(result)
        status = (f"✓ {result.total_tests_found} tests — "
                  f"{result.total_tests_with_data} with data — "
                  f"{len(result.trim_groups)} group(s)")

        # Build summary HTML
        summary = [
            html.Div([html.Strong(f"{result.total_tests_found}"),
                       " matching tests in index"]),
            html.Div([html.Strong(f"{result.total_tests_with_data}"),
                       " with detail data loaded"]),
            html.Div([html.Strong(f"{len(result.trim_groups)}"),
                       " trim group(s)"]),
        ]
        for w in result.warnings:
            if w.startswith("  ↳"):
                summary.append(html.Div(
                    w, style={"color": "#666", "fontSize": "11px",
                              "fontFamily": "monospace",
                              "whiteSpace": "pre-wrap",
                              "marginLeft": "8px"}))
            else:
                summary.append(html.Div(
                    f"⚠ {w}", style={"color": "#d93025",
                                      "marginTop": "4px"}))

        with _lock:
            _state["result"] = results_serial
            _state["status"] = status
            _state["summary_html"] = summary
            _state["done"] = True
            _state["running"] = False

        _log(f"Done: {status}")

    except Exception as e:
        _log(f"Comparison error: {e}")
        _log(traceback.format_exc())
        with _lock:
            _state["error"] = str(e)
            _state["done"] = True
            _state["running"] = False

    set_progress(0, "")


# ═════════════════════════════════════════════════════════════
# 2b. POLL — interval checks thread, updates everything
# ═════════════════════════════════════════════════════════════

@callback(
    # Status bar
    Output("status-line-1", "children"),
    Output("status-line-2", "children"),
    # Progress bar
    Output("progress-bar-fill", "style"),
    Output("progress-label", "children"),
    Output("progress-bar-wrap", "className"),
    # Results (only when thread finishes)
    Output("comparison-results", "data", allow_duplicate=True),
    Output("run-status", "children", allow_duplicate=True),
    Output("results-summary", "children", allow_duplicate=True),
    Output("results-summary", "className", allow_duplicate=True),
    # PX data (manual mode writes synthetic baseline here)
    Output("px-data", "data", allow_duplicate=True),
    # Trigger
    Input("status-interval", "n_intervals"),
    prevent_initial_call=True,
)
def poll_job(n):
    from data.log_buffer import get_recent, get_progress

    # Status bar text
    lines = get_recent(2)
    line1 = lines[-1] if len(lines) >= 1 else "Ready"
    line2 = lines[-2] if len(lines) >= 2 else ""

    # Progress bar
    pct, label = get_progress()
    bar_style = {"width": f"{pct}%"}
    bar_class = ("progress-bar-wrap"
                 if 0 < pct < 100
                 else "progress-bar-wrap hidden")

    no_results = (line1, line2,
                  bar_style, label, bar_class,
                  no_update, no_update, no_update, no_update,
                  no_update)

    # ── Check auto-mode thread ────────────────────────────
    with _lock:
        auto_done = _state["done"]

    if auto_done:
        with _lock:
            result = _state["result"]
            error = _state["error"]
            status = _state["status"]
            summary = _state["summary_html"]
        _reset()

        if error:
            return (line1, line2,
                    bar_style, label, bar_class,
                    {"error": True}, f"✗ Error: {error}",
                    [html.Div(f"✗ {error}",
                              style={"color": "#d93025"})],
                    "summary-card", no_update)

        return (line1, line2,
                bar_style, label, bar_class,
                result, status, summary,
                "summary-card", no_update)

    # ── Check manual-mode thread ──────────────────────────
    try:
        from manual_mode.callbacks import check_manual_result
        mm = check_manual_result()
        if mm is not None:
            return (line1, line2,
                    bar_style, label, bar_class,
                    mm["result"], mm["status"],
                    mm["summary"], "summary-card",
                    mm.get("px_data", no_update))
    except ImportError:
        pass

    # Neither thread finished — update status/progress only
    return no_results


# ═════════════════════════════════════════════════════════════
# 2c. RE-ENABLE BUTTON — fires when results arrive
# ═════════════════════════════════════════════════════════════

@callback(
    Output("run-btn", "disabled", allow_duplicate=True),
    Output("run-btn", "children", allow_duplicate=True),
    Input("comparison-results", "data"),
    prevent_initial_call=True,
)
def reenable_button(data):
    if data is not None:
        return False, "Run Comparison"
    return no_update, no_update

# ═════════════════════════════════════════════════════════════
# 3. SIMPLE READOUTS
# ═════════════════════════════════════════════════════════════

@callback(
    Output("total-tests-readout", "children"),
    Output("total-data-readout", "children"),
    Output("total-groups-readout", "children"),
    Input("comparison-results", "data"),
    prevent_initial_call=True,
)
def update_readouts(data):
    if not data:
        return "—", "—", "—"
    return (
        str(data.get("total_found", "—")),
        str(data.get("total_with_data", "—")),
        str(data.get("total_groups", "—")),
    )


# ═════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════

def _serialize_px(px):
    import numpy as np
    def _to_list(x):
        if x is None:
            return None
        return x.tolist() if isinstance(x, np.ndarray) else x

    trims = []
    for trim in px.trims:
        trims.append({
            "diameter": float(trim.diameter),
            "head_flow": _to_list(trim.head_flow),
            "head": _to_list(trim.head),
            "power_flow": _to_list(trim.power_flow),
            "power": _to_list(trim.power),
            "bep_flow": float(trim.bep_flow) if trim.bep_flow else None,
            "bep_efficiency": float(trim.bep_efficiency) if trim.bep_efficiency else None,
        })
    rated_dia = px.diameters[0] if px.diameters else 0
    return {
        "model": px.curve_number,
        "rated_dia": float(rated_dia),
        "rated_speed": float(px.rated_speed),
        "trims": trims,
        "pump_type": px.pump_type
    }


def _serialize_results(result):
    import numpy as np
    def _to_list(x):
        if x is None:
            return None
        if isinstance(x, np.ndarray):
            return x.tolist()
        return x

    groups = []
    for grp in result.trim_groups:
        tests = []
        for t in grp.tests:
            tests.append({
                "test_id": t.test_id,
                "source_index": t.source_index,
                "test_date": t.test_date,
                "trim_diameter": float(t.trim_diameter) if t.trim_diameter else None,
                "rpm": float(t.rpm) if t.rpm else None,
                "pass_fail": t.pass_fail or "",
                "num_stages": t.num_stages,
                "filing_info": t.filing_info or "",
                "upper_diameter": float(t.upper_diameter) if t.upper_diameter else None,
                "lower_diameter": float(t.lower_diameter) if t.lower_diameter else None,
                "is_polished": bool(t.is_polished),
                "mixed_trim":bool(getattr(t, "mixed_trim", False)),
                "raw_flow": _to_list(t.raw_flow),
                "raw_head": _to_list(t.raw_head),
                "raw_power": _to_list(t.raw_power),
                "raw_efficiency": _to_list(t.raw_efficiency),
                "scaled_flow": _to_list(t.scaled_flow),
                "scaled_head": _to_list(t.scaled_head),
                "scaled_power": _to_list(t.scaled_power),
                "scaled_efficiency": _to_list(t.scaled_efficiency),
                "affinity_warning": t.affinity_warning or "",
                "has_data": t.scaled_flow is not None,
            })
        bt = grp.baseline_trim  # TrimCurve or None
        groups.append({
            "nominal_diameter": str(grp.nominal_diameter),
            "baseline_diameter": float(bt.diameter) if bt else None,
            "diameter_ratio": grp.diameter_ratio,
            "affinity_applied": grp.affinity_applied,
            "tests": tests,
            "px_flow": _to_list(bt.head_flow) if bt else None,
            "px_head": _to_list(bt.head) if bt else None,
            "px_power_flow": _to_list(bt.power_flow) if bt else None,
            "px_power": _to_list(bt.power) if bt else None,
            "px_bep_flow": float(bt.bep_flow) if bt and bt.bep_flow else None,
            "px_bep_efficiency": float(bt.bep_efficiency) if bt and bt.bep_efficiency else None,
        })
    return {
        "groups": groups,
        "total_found": result.total_tests_found,
        "total_with_data": result.total_tests_with_data,
        "total_groups": len(result.trim_groups),
        "warnings": result.warnings,
    }