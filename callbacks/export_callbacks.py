# callbacks/export_callbacks.py
#
# Callbacks for:
#   - Export PSD Curves   (→ SaveFileDialog → psd_exporter.export_psd)
#   - Session Save        (→ SaveFileDialog → JSON dump)
#   - Session Load        (→ OpenFileDialog → JSON load → store updates)
#   - Cache Clear         (size indicator + confirmation + wipe)

import json
import os
import sys
import subprocess
import platform
import tempfile
import shutil
from pathlib import Path

from dash import callback, Input, Output, State, no_update, ctx
from dash.exceptions import PreventUpdate
from config import BASE_DIR, CACHE_DIR, CACHE_DB_PATH


# ── Cache limit (bytes) — 500 MB keeps the app fast on local machines
#    while caching ~150-200 detail .xlsm files for quick re-access.
CACHE_LIMIT_BYTES = 500 * 1024 * 1024   # 500 MB

SESSION_VERSION = 1


def _log(msg):
    print(f"[export] {msg}")

def get_resource_path(filename):
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / filename
    return Path(__file__).parent / filename

# =====================================================================
# FILE DIALOG HELPERS  (same PowerShell pattern as browse_for_px_file)
# =====================================================================

def _open_file_dialog(title="Select File", filter_str=None,
                      save=False, default_ext=".xlsx"):
    """
    Open a native file dialog via PowerShell (Windows) or tkinter.

    Parameters
    ----------
    title : str
    filter_str : str   PowerShell-style filter, e.g.
                       "Excel Files (*.xlsx)|*.xlsx|All (*.*)|*.*"
    save : bool        True = SaveFileDialog, False = OpenFileDialog
    default_ext : str  Default extension for save dialogs

    Returns
    -------
    str or None   Chosen path, or None if cancelled.
    """
    if filter_str is None:
        filter_str = "All Files (*.*)|*.*"

    try:
        if platform.system() == "Windows":
            dialog_type = ("SaveFileDialog" if save
                           else "OpenFileDialog")
            ps_script = (
                'Add-Type -AssemblyName System.Windows.Forms; '
                f'$f = New-Object System.Windows.Forms.{dialog_type}; '
                f'$f.Title = "{title}"; '
                f'$f.Filter = "{filter_str}"; '
                '$f.TopMost = $true; '
            )
            if save:
                ps_script += (
                    f'$f.DefaultExt = "{default_ext}"; '
                    '$f.OverwritePrompt = $true; '
                )
            ps_script += (
                '$null = $f.ShowDialog(); '
                'Write-Output $f.FileName'
            )
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True, text=True, timeout=120,
            )
        else:
            # Fallback: tkinter
            if save:
                tk_method = (
                    "f = filedialog.asksaveasfilename("
                    f"  parent=root, title='{title}', "
                    f"  defaultextension='{default_ext}', "
                    "  filetypes=[('Excel','*.xlsx'),('All','*.*')]); "
                )
            else:
                tk_method = (
                    "f = filedialog.askopenfilename("
                    f"  parent=root, title='{title}', "
                    "  filetypes=[('JSON','*.json'),('All','*.*')]); "
                )
            result = subprocess.run(
                [sys.executable, "-c",
                 "import tkinter as tk; "
                 "from tkinter import filedialog; "
                 "root = tk.Tk(); root.withdraw(); "
                 "root.attributes('-topmost', True); "
                 "root.lift(); root.focus_force(); "
                 + tk_method +
                 "print(f); root.destroy()"],
                capture_output=True, text=True, timeout=120,
            )

        path = result.stdout.strip()
        if result.returncode != 0 and result.stderr:
            _log(f"Dialog error: {result.stderr[:200]}")
        return path if path else None

    except subprocess.TimeoutExpired:
        _log("File dialog timed out")
        return None
    except Exception as e:
        _log(f"File dialog failed: {e}")
        return None


# =====================================================================
# 1. EXPORT PSD CURVES
# =====================================================================

@callback(
    Output("export-psd-status", "children"),
    Input("export-psd-btn", "n_clicks"),
    State("px-data", "data"),
    State("comparison-results", "data"),
    State("fit-settings", "data"),
    State("shape-settings", "data"),
    State("test-visibility", "data"),
    State("group-visibility", "data"),
    State("added-trims", "data"),
    prevent_initial_call=True,
)
def export_psd_curves(n_clicks, px_data, comp_data, fit_settings,
                      shape_settings, test_vis, group_vis, added_trims):
    
    # ── Default template path (bundled with app) ─────────────────
    PSD_TEMPLATE_PATH = str(get_resource_path("PSD_Format.xlsx"))

    if not n_clicks:
        return no_update
    if not px_data or not comp_data:
        return "⚠ Load PX file and run comparison first."

    # Check template exists
    if not os.path.isfile(PSD_TEMPLATE_PATH):
        return f"⚠ Template not found: {PSD_TEMPLATE_PATH}"

    # Prompt for save location
    model = px_data.get("model", "export")
    default_name = f"{model}_PSD.xlsx"
    save_path = _open_file_dialog(
        title="Save PSD Curves",
        filter_str=(
            "Excel Files (*.xlsx)|*.xlsx|All Files (*.*)|*.*"
        ),
        save=True,
        default_ext=".xlsx",
    )
    if not save_path:
        return "Export cancelled."

    # Ensure .xlsx extension
    if not save_path.lower().endswith(".xlsx"):
        save_path += ".xlsx"

    try:
        from data.psd_exporter import export_psd
        
        print("output_path is: ", save_path)
        print("px_data is: ", px_data)
        print("comp_data is: ", comp_data)
        print("fit_settings is: ",fit_settings)
        print("shape_settings is: ", shape_settings)
        print("test_visibility is: ", test_vis)
        print("group_visibility is: ",group_vis)
        print("added_trims is: ", added_trims)

        count = export_psd(
            template_path=PSD_TEMPLATE_PATH,
            output_path=save_path,
            px_data=px_data,
            comp_data=comp_data,
            fit_settings=fit_settings or {},
            shape_settings=shape_settings or {},
            test_visibility=test_vis or {},
            group_visibility=group_vis or {},
            added_trims=added_trims or [],
        )
        return f"✓ Exported {count} trim(s) → {os.path.basename(save_path)}"
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        
        _log(f"Export error: {e}")
        return f"⚠ Export failed: {e}"


# =====================================================================
# 2. SESSION SAVE
# =====================================================================

@callback(
    Output("session-status", "children"),
    Input("save-session-btn", "n_clicks"),
    State("px-file-path", "value"),
    State("date-start", "value"),
    State("date-end", "value"),
    State("trim-tolerance", "value"),
    State("band-pct", "value"),
    State("show-baseline", "value"),
    State("unit-toggle", "value"),
    State("fit-settings", "data"),
    State("shape-settings", "data"),
    State("test-visibility", "data"),
    State("group-visibility", "data"),
    State("added-trims", "data"),
    prevent_initial_call=True,
)
def save_session(n_clicks, px_path, date_start, date_end,
                 trim_tol, band_pct, show_baseline, unit_toggle,
                 fit_settings, shape_settings, test_vis,
                 group_vis, added_trims):
    if not n_clicks:
        return no_update

    session = {
        "version": SESSION_VERSION,
        "px_file_path": px_path or "",
        "date_start": date_start or "",
        "date_end": date_end or "",
        "trim_tolerance": float(trim_tol) if trim_tol else 3.0,
        "band_pct": float(band_pct) if band_pct else 6.0,
        "show_baseline": bool(show_baseline),
        "unit_toggle": unit_toggle or "feet",
        "fit_settings": fit_settings or {},
        "shape_settings": shape_settings or {},
        "test_visibility": test_vis or {},
        "group_visibility": group_vis or {},
        "added_trims": added_trims or [],
    }

    save_path = _open_file_dialog(
        title="Save Session",
        filter_str="JSON Files (*.json)|*.json|All Files (*.*)|*.*",
        save=True,
        default_ext=".json",
    )
    if not save_path:
        return "Save cancelled."

    if not save_path.lower().endswith(".json"):
        save_path += ".json"

    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2, default=str)
        return f"✓ Session saved → {os.path.basename(save_path)}"
    except Exception as e:
        _log(f"Save error: {e}")
        return f"⚠ Save failed: {e}"


# =====================================================================
# 3. SESSION LOAD
# =====================================================================

@callback(
    Output("px-file-path", "value", allow_duplicate=True),
    Output("date-start", "value", allow_duplicate=True),
    Output("date-end", "value", allow_duplicate=True),
    Output("trim-tolerance", "value", allow_duplicate=True),
    Output("band-pct", "value", allow_duplicate=True),
    Output("show-baseline", "value", allow_duplicate=True),
    Output("unit-toggle", "value", allow_duplicate=True),
    Output("fit-settings", "data", allow_duplicate=True),
    Output("shape-settings", "data", allow_duplicate=True),
    Output("test-visibility", "data", allow_duplicate=True),
    Output("group-visibility", "data", allow_duplicate=True),
    Output("added-trims", "data", allow_duplicate=True),
    Output("session-status", "children", allow_duplicate=True),
    Input("load-session-btn", "n_clicks"),
    prevent_initial_call=True,
)
def load_session(n_clicks):
    if not n_clicks:
        raise PreventUpdate

    load_path = _open_file_dialog(
        title="Load Session",
        filter_str="JSON Files (*.json)|*.json|All Files (*.*)|*.*",
        save=False,
    )
    if not load_path:
        return (no_update,) * 12 + ("Load cancelled.",)

    try:
        with open(load_path, "r", encoding="utf-8") as f:
            session = json.load(f)
    except Exception as e:
        _log(f"Load error: {e}")
        return (no_update,) * 12 + (f"⚠ Load failed: {e}",)

    v = session.get("version", 1)
    if v != SESSION_VERSION:
        _log(f"Session version {v}, expected {SESSION_VERSION}")

    baseline_val = session.get("show_baseline", True)
    if isinstance(baseline_val, bool):
        baseline_val = ["on"] if baseline_val else []

    return (
        session.get("px_file_path", ""),
        session.get("date_start", "") or None,
        session.get("date_end", "") or None,
        session.get("trim_tolerance", 3.0),
        session.get("band_pct", 6.0),
        baseline_val,
        session.get("unit_toggle", "feet"),
        session.get("fit_settings", {}),
        session.get("shape_settings", {}),
        session.get("test_visibility", {}),
        session.get("group_visibility", {}),
        session.get("added_trims", []),
        f"✓ Session loaded from {os.path.basename(load_path)}",
    )


# =====================================================================
# 4. CACHE SIZE INDICATOR
# =====================================================================

def _get_cache_size_bytes():
    """Compute total size of clearable cache data."""
    total = 0

    # 1. SQLite cache DB
    if os.path.isfile(CACHE_DB_PATH):
        total += os.path.getsize(CACHE_DB_PATH)

    # 2. Temp detail file copies
    # Import the module (not the variable) so we read the live value
    import data.excel_reader as _er
    cache_dir = getattr(_er, '_local_cache_dir', None)
    if cache_dir and os.path.isdir(cache_dir):
        for f in os.listdir(cache_dir):
            fp = os.path.join(cache_dir, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)

    # 3. Log files
    logs_dir = BASE_DIR / "logs"
    if logs_dir.is_dir():
        for f in logs_dir.iterdir():
            if f.is_file():
                total += f.stat().st_size

    return total


def _format_size(b):
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    else:
        return f"{b / (1024 * 1024):.1f} MB"


@callback(
    Output("cache-size-label", "children"),
    Output("cache-bar-fill", "style"),
    Output("cache-bar-fill", "className"),
    Output("cache-bar-numbers", "children"),
    Input("cache-refresh-interval", "n_intervals"),
)
def update_cache_size(_n):
    try:
        size = _get_cache_size_bytes()
        limit = CACHE_LIMIT_BYTES
        pct = min(size / limit * 100, 100) if limit > 0 else 0

        size_mb = size / (1024 * 1024)
        limit_mb = limit / (1024 * 1024)
        numbers = f"{size_mb:.0f} / {limit_mb:.0f} MB"

        # Color coding: green < 60%, amber 60-85%, red > 85%
        if pct < 60:
            bar_cls = "cache-bar-fill cache-bar-ok"
        elif pct < 85:
            bar_cls = "cache-bar-fill cache-bar-warn"
        else:
            bar_cls = "cache-bar-fill cache-bar-crit"

        label = f"{pct:.0f}%"
        return label, {"width": f"{pct:.1f}%"}, bar_cls, numbers

    except Exception:
        return "–", {"width": "0%"}, "cache-bar-fill", "0 / 500 MB"


# =====================================================================
# 5. CACHE CLEAR
# =====================================================================

@callback(
    Output("cache-clear-status", "children"),
    Output("cache-size-label", "children", allow_duplicate=True),
    Output("cache-bar-fill", "style", allow_duplicate=True),
    Output("cache-bar-fill", "className", allow_duplicate=True),
    Output("cache-bar-numbers", "children", allow_duplicate=True),
    Input("clear-cache-btn", "n_clicks"),
    prevent_initial_call=True,
)
def clear_cache(n_clicks):
    if not n_clicks:
        return no_update, no_update, no_update, no_update, no_update

    cleared = []
    errors = []

    # Show size before clearing
    try:
        pre_size = _get_cache_size_bytes()
    except Exception:
        pre_size = 0

    # 1. Temp detail file copies
    try:
        import data.excel_reader as _er
        cache_dir = getattr(_er, '_local_cache_dir', None)
        if cache_dir and os.path.isdir(cache_dir):
            count = 0
            for f in os.listdir(cache_dir):
                fp = os.path.join(cache_dir, f)
                if os.path.isfile(fp):
                    os.remove(fp)
                    count += 1
            cleared.append(f"{count} temp file(s)")
    except Exception as e:
        errors.append(f"temp files: {e}")

    # 2. Log files
    try:
        logs_dir = BASE_DIR / "logs"
        if logs_dir.is_dir():
            count = 0
            for f in logs_dir.iterdir():
                if f.is_file():
                    f.unlink()
                    count += 1
            cleared.append(f"{count} log file(s)")
    except Exception as e:
        errors.append(f"logs: {e}")

    # 3. SQLite cache — drop tables then VACUUM to reclaim disk space
    #    (DROP TABLE alone marks pages free internally but the .db file
    #    stays the same size on disk without VACUUM)
    try:
        from data.cache_db import CacheDB
        db = CacheDB()
        db.clear_all()
        # VACUUM must run outside a transaction
        import sqlite3
        conn = sqlite3.connect(str(CACHE_DB_PATH))
        conn.execute("VACUUM")
        conn.close()
        cleared.append("cache DB")
    except Exception as e:
        errors.append(f"cache DB: {e}")

    # 4. In-memory file path caches
    try:
        from data.path_resolver import clear_file_cache, clear_tree_index
        clear_file_cache()
        clear_tree_index()
        cleared.append("path index")
    except Exception:
        pass  # Non-critical

    msg = f"✓ Cleared {_format_size(pre_size)}"
    if cleared:
        msg += f" ({', '.join(cleared)})"
    if errors:
        msg += f"  ⚠ Errors: {'; '.join(errors)}"

    # Immediately refresh the bar with post-clear size
    try:
        post_size = _get_cache_size_bytes()
        limit = CACHE_LIMIT_BYTES
        pct = min(post_size / limit * 100, 100) if limit > 0 else 0
        size_mb = post_size / (1024 * 1024)
        limit_mb = limit / (1024 * 1024)
        numbers = f"{size_mb:.0f} / {limit_mb:.0f} MB"
        bar_cls = ("cache-bar-fill cache-bar-ok" if pct < 60
                   else "cache-bar-fill cache-bar-warn" if pct < 85
                   else "cache-bar-fill cache-bar-crit")
        return (msg, f"{pct:.0f}%", {"width": f"{pct:.1f}%"},
                bar_cls, numbers)
    except Exception:
        return msg, "0%", {"width": "0%"}, "cache-bar-fill cache-bar-ok", "0 / 500 MB"


# =====================================================================
# 5b. OPEN LOGS FOLDER
# =====================================================================

@callback(
    Output("logs-status", "children"),
    Input("open-logs-btn", "n_clicks"),
    prevent_initial_call=True,
)
def open_logs_folder(n_clicks):
    if not n_clicks:
        return no_update

    logs_dir = BASE_DIR / "logs"
    logs_dir.mkdir(exist_ok=True)

    try:
        if platform.system() == "Windows":
            os.startfile(str(logs_dir))
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(logs_dir)])
        else:
            subprocess.Popen(["xdg-open", str(logs_dir)])
        return ""
    except Exception as e:
        # Fallback: show the path so they can navigate manually
        _log(f"Could not open logs folder: {e}")
        return f"📂 {logs_dir}"


# =====================================================================
# 6. REBUILD PANELS AFTER SESSION LOAD
# =====================================================================
# When a session is loaded, the stores update but existing trim-group
# card HTML still reflects old defaults.  If comparison-results exist,
# rebuild the panels with the freshly loaded settings.

@callback(
    Output("trim-group-panels", "children", allow_duplicate=True),
    Output("virtual-group-panels", "children", allow_duplicate=True),
    Input("session-status", "children"),
    State("comparison-results", "data"),
    State("px-data", "data"),
    State("fit-settings", "data"),
    State("shape-settings", "data"),
    State("group-visibility", "data"),
    State("test-visibility", "data"),
    State("added-trims", "data"),
    prevent_initial_call=True,
)
def rebuild_panels_after_session_load(
        session_msg, comp_data, px_data,
        fit_settings, shape_settings,
        group_vis, test_vis, added_trims):
    """Rebuild trim group cards when a session is loaded."""
    from dash import html
    chart_type = ["head", "power"]
    # Only trigger on successful load
    if not session_msg or "Session loaded" not in str(session_msg):
        return no_update, no_update

    if not comp_data or "groups" not in comp_data:
        return no_update, no_update

    from layout.main_layout import (build_trim_group_card,
                                     build_virtual_trim_card,
                                     VIRTUAL_INDEX_OFFSET)

    # -- Rebuild real group panels ------------------------------------
    panels = []
    for i, grp in enumerate(comp_data.get("groups", [])):
        for chart in chart_type:
            panels.append(build_trim_group_card(
                i, grp,
                chart_type=chart or {},
                fit_settings=fit_settings or {},
                shape_settings=shape_settings or {},
                group_visibility=group_vis or {},
                test_visibility=test_vis or {},
            ))
    if not panels:
        panels = [html.P("No matching tests found.",
                         className="empty-message")]

    # -- Rebuild virtual group panels ---------------------------------
    vpanels = []
    if added_trims and comp_data and px_data:
        px_diameters = [t["diameter"] for t in px_data.get("trims", [])]
        all_tests = []
        for grp in comp_data.get("groups", []):
            for t in grp.get("tests", []):
                if t.get("has_data"):
                    all_tests.append(t)

        for vi, vt_info in enumerate(added_trims):
            v_dia = float(vt_info["diameter"])
            virtual_tests = []
            for t in all_tests:
                td = t.get("trim_diameter")
                if td is None:
                    continue
                if abs(td - v_dia) < min(
                        abs(td - d) for d in px_diameters):
                    virtual_tests.append(t)

            vpanels.append(build_virtual_trim_card(
                vi, vt_info, virtual_tests,
                fit_settings=fit_settings or {},
                shape_settings=shape_settings or {},
                group_visibility=group_vis or {},
                test_visibility=test_vis or {},
            ))

    return panels, vpanels
