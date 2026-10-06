# config.py
#
# Configuration for the Pump Test Comparison Tool.
#
# DEV vs PRODUCTION:
#   - "dev":  all files in the project base directory
#   - "prod": index files and detail files on network drives

import sys
from pathlib import Path

# ── Environment ──────────────────────────────────────────────
ENVIRONMENT = "Prod"

# ── Base directory detection ─────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

# ── Local Cache ──────────────────────────────────────────────
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_DB_PATH = CACHE_DIR / "local_cache.db"


# ═════════════════════════════════════════════════════════════════════
# INDEX FILE DEFINITIONS
#
# Each entry maps standard field names → actual Excel column headers.
# The rest of the app always uses the standard names.
# ═════════════════════════════════════════════════════════════════════

if ENVIRONMENT == "dev":
    INDEX_FILES = [
        {
            "name": "new_test_list",
            "path": str(BASE_DIR / "NEWTESTLISTv.2.xlsx"),
            "sheet": 0,
            "columns": {
                "test_id":        "Test ID",
                "job_number":     "Job Number",
                "order_type":     "Order Type",
                "pump_model":     "Pump Model",
                "bowl_size":      "Bowl Size",
                "rated_flow":     "Q (GPM)",
                "rated_head":     "TDH (ft)",
                "guaranteed_eff": "Guaranteed Efficiency (%)",
                "rpm":            "RPM",
                "pass_fail":      "Pass / Fail",
                "test_type":      "Test Type",
                "test_run":       "Test Run",
                "num_stages":     "Number of Stages",
                "configuration":  "Configuration",
                "mixed_bowl":     "Mixed Bowl",
                "first_stg_size": "1st Stage Bowl Size",
                "first_stg_model":"1st Stage Bowl Model",
                "impeller_trim":  "Top Impeller Trim",
                "imp_part_num":   "Top Impeller Part Number",
                "imp_material":   "Top Impeller Material",
                "mixed_impellers":"Mixed Impellers",
                "first_stg_imp":  "1st STG Impeller Part Number",
                "first_stg_mat":  "1st STG Impeller Material",
                "first_stg_trim": "1st STG Impeller Trim",
                "bowl_material":  "Bowl Material",
                "customer":       "Customer Name",
                "fire_pump":      "Fire Pump?",
                "test_date":      "Test Date",
                "test_grade":     "Test Grade",
                "driver":         "Driver",
                "driver_speed":   "Driver True Speed",
                "trim_source":    "Trim Source",
                "trim_source_2":  "Trim Source 2",
                "failure_cause":  "Root Cause of Test Failure",
                "comments":       "Comments",
                "valid_test":     "Valid Test? (Yes or No)",
                "px_trim":        "PX Trim",
                "px_pass_fail":   "PX Pass/Fail",
                "px_eff":         "PX Eff (%)",
                "test_eff":       "Test Eff (%)",
                "test_head":      "Test Head (FT)",
                "test_bep_flow":  "Test BEP Flow (GPM)",
                "test_bep_head":  "Test BEP Head (FT)",
                "test_bep_eff":   "Test BEP (%)",
                "pattern_notes":  "Pattern Notes",
            },
        },
        {
            "name": "ppu_test_log",
            "path": str(BASE_DIR
                        / "PPU Daily Hor Inl Std Test Log Rotation.xlsx"),
            "sheet": 0,
            "columns": {
                "test_id":        "Test No.",
                "job_number":     "Shop Order No.:",
                "serial_number":  "Serial No.:",
                "pump_model":     "Pump Model:",
                "rated_flow":     "Rated Capacity (gpm):",
                "rated_head":     "Rated Head  ( ft ):",
                "rpm":            "RPM",
                "driver_hp":      "Driver HP",
                "pass_fail":      "Pass",
                "fire_pump":      "Fire",
                "test_date":      "Test Date:",
                "test_run":       "Test No..1",
                "rotation":       "Rotation",
                "service_factor": "S.F.",
                "group":          "Group",
                "model_type":     "Model Type",
                "test_stand":     "Test Stand",
                "flood_pump":     "Flood Pump",
                "material_1":     "MATL 1",
                "material_2":     "MATL 2",
                "cond_failure":   "Conditional Failure",
                "proc_failure":   "Process Failure",
                "corrective":     "corrective action",
                "test_type":      "Test",
            },
        },
    ]
else:
    # ── PRODUCTION: same column mappings, network paths ───────
    INDEX_FILES = [
        {
            "name": "new_test_list",
            "path": r"X:\US\Ind\Prod\Verttest\NEWTESTLISTv.2.xlsx",
            "sheet": 0,
            "columns": {
                "test_id":        "Test ID",
                "job_number":     "Job Number",
                "order_type":     "Order Type",
                "pump_model":     "Pump Model",
                "bowl_size":      "Bowl Size",
                "rated_flow":     "Q (GPM)",
                "rated_head":     "TDH (ft)",
                "guaranteed_eff": "Guaranteed Efficiency (%)",
                "rpm":            "RPM",
                "pass_fail":      "Pass / Fail",
                "test_type":      "Test Type",
                "test_run":       "Test Run",
                "num_stages":     "Number of Stages",
                "configuration":  "Configuration",
                "mixed_bowl":     "Mixed Bowl",
                "first_stg_size": "1st Stage Bowl Size",
                "first_stg_model":"1st Stage Bowl Model",
                "bowl_material":  "Bowl Material",
                "imp_part_num":   "Top Impeller Part Number",
                "imp_material":   "Top Impeller Material",
                "impeller_trim":  "Top Impeller Trim",
                "mixed_impellers":"Mixed Impellers",
                "first_stg_imp":  "1st STG Impeller Part Number",
                "first_stg_mat":  "1st STG Impeller Material",
                "first_stg_trim": "1st STG Impeller Trim",
                "customer":       "Customer Name",
                "fire_pump":      "Fire Pump?",
                "test_date":      "Test Date",
                "test_grade":     "Test Grade",
                "driver":         "Driver",
                "driver_speed":   "Driver True Speed",
                "trim_source":    "Trim Source",
                "trim_source_2":  "Trim Source 2",
                "failure_cause":  "Root Cause of Test Failure",
                "comments":       "Comments",
                "valid_test":     "Valid Test? (Yes or No)",
                "px_trim":        "PX Trim",
                "px_pass_fail":   "PX Pass/Fail",
                "px_eff":         "PX Eff (%)",
                "test_eff":       "Test Eff (%)",
                "test_head":      "Test Head (FT)",
                "test_bep_flow":  "Test BEP Flow (GPM)",
                "test_bep_head":  "Test BEP Head (FT)",
                "test_bep_eff":   "Test BEP (%)",
                "pattern_notes":  "Pattern Notes",
            },
        },
        {
            "name": "ppu_test_log",
            "path": r"X:\US\Ind\Prod\InsideTest\First Test Folder\PPU Daily Hor Inl Std Test Log Rotation.xlsx",
            "sheet": 0,
            "columns": {
                "test_id":        "Test No.",
                "job_number":     "Shop Order No.:",
                "serial_number":  "Serial No.:",
                "pump_model":     "Pump Model:",
                "rated_flow":     "Rated Capacity (gpm):",
                "rated_head":     "Rated Head  ( ft ):",
                "rpm":            "RPM",
                "driver_hp":      "Driver HP",
                "pass_fail":      "Pass",
                "fire_pump":      "Fire",
                "test_date":      "Test Date:",
                "test_run":       "Test No..1",
                "rotation":       "Rotation",
                "service_factor": "S.F.",
                "group":          "Group",
                "model_type":     "Model Type",
                "test_stand":     "Test Stand",
                "flood_pump":     "Flood Pump",
                "material_1":     "MATL 1",
                "material_2":     "MATL 2",
                "cond_failure":   "Conditional Failure",
                "proc_failure":   "Process Failure",
                "corrective":     "corrective action",
                "test_type":      "Test",
            },
        },
    ]


# ═════════════════════════════════════════════════════════════════════
# DETAIL FILE CONFIGURATION
#
# Detail .xlsm files contain test data in a sheet called "TEST DATA".
# Headers are on row 10, data starts at row 11.
#
# There are multiple data sections (column blocks). The app uses the
# "Purple" section: Corrected to Rated Speed (columns DN–EI).
#
# The columns below define which data the app extracts. Each key is
# a standard field name; the value is the actual Excel column header.
# ═════════════════════════════════════════════════════════════════════

DETAIL_SHEET_NAME = "TEST DATA"
DETAIL_HEADER_ROW = 10       # 1-based row number of the header
DETAIL_DATA_START_ROW = 11   # 1-based row where data begins

# ── Column mapping for the "Purple: Corrected to Rated Speed" section
# These are the primary columns for charting and analysis.
DETAIL_COLUMNS = {
    "curve_no":           "Curve No.",             # DN — curve identifier
    "flow":               "Capacity (GPM)",        # DO — flow rate
    "speed":              "Speed (RPM)",           # DP — rotational speed
    "power_in":           "Power In (HP)",         # DQ — total power in
    "power_bowl_motor":   "Power Bowl Cal Motor (HP)",   # DR — bowl power (cal motor)
    "power_bowl_dyno":    "Power Bowl Dyno (HP)",        # DS — bowl power (dyno)
    "power_pump_motor":   "Power Pump Cal Motor (HP)",   # DT — pump power (cal motor)
    "power_pump_dyno":    "Power Pump Dyno (HP)",        # DU — pump power (dyno)
    "tdh_bowl":           "TDH Bowl (FT)",         # DV — head (bowl)
    "tdh_pump":           "TDH Pump (FT)",         # DW — head (pump)
    "eff_bowl_motor":     "Eff Bowl Cal Motor (%)",# DX — efficiency (cal motor)
    "eff_bowl_dyno":      "Eff Bowl Dyno (%)",     # DY — efficiency (dyno)
    "eff_pump_motor":     "Eff Pump Cal Motor (%)",# DZ — efficiency pump (cal motor)
    "eff_pump_dyno":      "Eff Pump Dyno (%)",     # EA — efficiency pump (dyno)
    "eff_overall":        "Eff Overall (%)",       # EB — overall efficiency
    "npsha":              "NPSHA Test (FT)",       # EC
    "lift":               "Lift (FT)",             # ED
}

# ── Which section to read from the spreadsheet ───────────────
# The same columns (flow, head, power, etc.) appear multiple times
# in different sections. This tells the reader which OCCURRENCE of
# each header to use.
#
# Section indices (0-based occurrence of duplicated headers):
#   0 = Blue  (Corrected to Curve Sheet units)
#   1 = Purple (Corrected to Rated Speed)     ← DEFAULT
#   2 = Dark Blue (Plotting "As Run")
#   3 = Dark Purple (Plotting "Rated Speed")
DETAIL_SECTION_INDEX = 1   # Use the Purple section by default

# ── Columns used by the chart callbacks ──────────────────────
# These map to the standard names used in chart_callbacks.py.
# "flow" is the X-axis for all charts.
# For Y-axis, the user chooses between bowl/pump and motor/dyno.
CHART_Y_COLUMNS = {
    "tdh": {
        "bowl":  "tdh_bowl",     # TDH Bowl (FT) — default for head charts
        "pump":  "tdh_pump",     # TDH Pump (FT)
    },
    "power": {
        "bowl_motor":  "power_bowl_motor",   # Power Bowl Cal Motor (HP)
        "bowl_dyno":   "power_bowl_dyno",    # Power Bowl Dyno (HP)
        "pump_motor":  "power_pump_motor",   # Power Pump Cal Motor (HP)
        "pump_dyno":   "power_pump_dyno",    # Power Pump Dyno (HP)
    },
    "efficiency": {
        "bowl_motor":  "eff_bowl_motor",     # Eff Bowl Cal Motor (%)
        "bowl_dyno":   "eff_bowl_dyno",      # Eff Bowl Dyno (%)
        "pump_motor":  "eff_pump_motor",     # Eff Pump Cal Motor (%)
        "pump_dyno":   "eff_pump_dyno",      # Eff Pump Dyno (%)
        "overall":     "eff_overall",        # Eff Overall (%)
    },
}


# ── Detail file paths ────────────────────────────────────────
#
# PPU (inline) detail files are organized by year:
#   \\server\base\2024\{test_id}.xlsm
#   \\server\base\2025\{test_id}.xlsm
#
# Vertical pump detail files are in a flat directory.
#
if ENVIRONMENT == "dev":
    # Dev: all detail files in the project base directory
    DETAIL_FILE_DIR = r"X:\US\Ind\Proj\Manufacturing\Voyage\TestData" # Model name folders expected here too
    PPU_DETAIL_BASE = r"X:\US\Ind\Prod\InsideTest"   # Year subfolders expected here too
else:
    # Production: update these to your actual network paths
    DETAIL_FILE_DIR = r"X:\US\Ind\Proj\Manufacturing\Voyage\TestData"
    PPU_DETAIL_BASE = r"X:\US\Ind\Prod\InsideTest"

# Mapping from index name → detail base directory
DETAIL_DIRS = {
    "ppu_test_log":  PPU_DETAIL_BASE,   # Has year subfolders
    "new_test_list": DETAIL_FILE_DIR,    # Flat directory
}

# Which index types use year-folder organization
YEAR_FOLDER_INDEXES = {"ppu_test_log"}

# ── Future DB ────────────────────────────────────────────────
# DB_CONNECTION_STRING = "postgresql://user:pass@host:5432/dbname"