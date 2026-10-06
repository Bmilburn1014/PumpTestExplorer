# data/px_curves.py
#
# Reads PX base curve files produced by the external curve generation tool.
#
# FILE STRUCTURE:
#   Sheet "Curve Header Data":
#     Row 11: curve_number (A), revision (B), speed (C), poles (D), hz (E)
#
#   Sheet named after curve number (e.g., "2.5PVF8-3520"):
#     Column A, rows 10+:  List of impeller diameters (inches)
#     Row 7:               Diameter value repeated at each block start
#     Row 8:               Column headers
#     Rows 10-85:          Spline knot data
#
#     Each diameter occupies a 21-column block:
#       Offset 0-6:   Head   → [Flow, Head, Disabled, OnCurve, DivPt, SlopeEn, SlopeVal]
#       Offset 7-13:  Power  → [Flow, Power, Disabled, OnCurve, DivPt, SlopeEn, SlopeVal]
#       Offset 14-20: NPSH   → [Flow, NPSH,  Disabled, OnCurve, DivPt, SlopeEn, SlopeVal]
#
#     Block 1 starts at column 4 (D). Subsequent blocks are +21 cols apart.

import numpy as np
from dataclasses import dataclass, field
from pathlib import Path


# ── Column offsets within each 21-column diameter block ──────
BLOCK_WIDTH = 21
BLOCK_START_COL = 4   # Column D = index 4 (1-based)

HEAD_FLOW_OFFSET = 0
HEAD_VAL_OFFSET = 1
POWER_FLOW_OFFSET = 7
POWER_VAL_OFFSET = 8
NPSH_FLOW_OFFSET = 14
NPSH_VAL_OFFSET = 15

# Row references (1-based)
DIAMETER_ROW = 7
DATA_START_ROW = 10
DATA_END_ROW = 39

# Metadata in row 6/7
BEP_FLOW_OFFSET = 9
BEP_EFF_OFFSET = 10
SHUTOFF_PWR_OFFSET = 15


@dataclass
class TrimCurve:
    """Performance curves for a single impeller trim diameter."""
    diameter: float
    head_flow: np.ndarray = None
    head: np.ndarray = None
    power_flow: np.ndarray = None
    power: np.ndarray = None
    npsh_flow: np.ndarray = None
    npsh: np.ndarray = None
    bep_flow: float = None
    bep_efficiency: float = None
    shutoff_power: float = None


@dataclass
class PXCurveSet:
    """Complete set of baseline curves from a PX file."""
    curve_number: str = ""
    revision: str = ""
    rated_speed: float = 0
    poles: int = 0
    frequency: float = 0
    units_flow: str = "USgpm"
    units_head: str = "ft"
    units_power: str = "hp"
    source_file: str = ""
    trims: list = field(default_factory=list)
    pump_type: str = ""

    @property
    def diameters(self) -> list[float]:
        return sorted([t.diameter for t in self.trims], reverse=True)

    def get_trim(self, diameter: float) -> TrimCurve | None:
        for t in self.trims:
            if abs(t.diameter - diameter) < 0.001:
                return t
        return None

    def find_nearest_trim(self, diameter: float) -> TrimCurve | None:
        if not self.trims:
            return None
        return min(self.trims, key=lambda t: abs(t.diameter - diameter))

    def find_bracketing_trims(self, diameter: float) -> tuple:
        diameters = self.diameters
        if not diameters:
            return None, None
        if diameter >= diameters[0]:
            return self.get_trim(diameters[0]), None
        if diameter <= diameters[-1]:
            return self.get_trim(diameters[-1]), None
        for i in range(len(diameters) - 1):
            if diameters[i] >= diameter >= diameters[i + 1]:
                return (self.get_trim(diameters[i + 1]),
                        self.get_trim(diameters[i]))
        return self.find_nearest_trim(diameter), None


def read_px_curves(filepath: str) -> PXCurveSet:
    """
    Read a PX base curves file and return all trim curves.

    Uses pandas for fast bulk reads instead of cell-by-cell openpyxl
    access, which can hang on large files.
    """
    import pandas as pd

    filepath = str(filepath)
    result = PXCurveSet(source_file=filepath)
    # ── Read Curve Header Data (small — use pandas) ──────────
    try:
        hdr = pd.read_excel(filepath, sheet_name="Curve Header Data",
                            header=None, engine="openpyxl",
                            nrows=15)
        # Row 11 (0-indexed row 10) has the curve data
        if len(hdr) >= 11:
            row = hdr.iloc[10]
            result.curve_number = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
            result.revision = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""
            result.rated_speed = _num(row.iloc[2]) or 0
            result.poles = int(_num(row.iloc[3]) or 0)
            result.frequency = _num(row.iloc[4]) or 0

        # Units from rows 1-4 (0-indexed 0-3)
        if len(hdr) >= 4:
            result.units_flow = str(hdr.iloc[0, 1]) if pd.notna(hdr.iloc[0, 1]) else "USgpm"
            result.units_head = str(hdr.iloc[1, 1]) if pd.notna(hdr.iloc[1, 1]) else "ft"
            result.units_power = str(hdr.iloc[2, 1]) if pd.notna(hdr.iloc[2, 1]) else "hp"
    except Exception as e:
        print(f"Warning: Could not read Curve Header Data: {e}")

    # ── Fallback 1: Try "Curve Header (IEQ use only)" sheet ──
    if result.rated_speed == 0:
        try:
            ieq = pd.read_excel(filepath,
                                sheet_name="Curve Header (IEQ use only)",
                                header=None, engine="openpyxl",
                                nrows=15)
            if len(ieq) >= 11:
                row = ieq.iloc[10]
                spd = _num(row.iloc[2])
                if spd and spd > 0:
                    result.rated_speed = spd
                    print(f"Speed from IEQ header sheet: {spd}")
                # Also fill curve_number if still missing
                if not result.curve_number:
                    cn = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
                    if cn:
                        result.curve_number = cn
        except Exception:
            pass  # Sheet may not exist

    # ── Discover the curve data sheet name early ─────────────
    # This must happen BEFORE the curve-number-based speed fallback,
    # because when header sheets are missing the curve number comes
    # from the sheet tab name (e.g. "5PVF7-3520").
    import openpyxl
    wb_ro = openpyxl.load_workbook(filepath, read_only=True,
                                   data_only=True)
    sheet_names = wb_ro.sheetnames
    wb_ro.close()

    curve_sheet_name = None
    skip_names = {"Curve Header (IEQ use only)", "Curve Header Data"}
    for name in sheet_names:
        if name not in skip_names:
            curve_sheet_name = name
            if not result.curve_number:
                result.curve_number = name.strip()
            break

    # ── Fallback 2: Extract speed from curve number string ───
    #    e.g., "5PVF7-3520" → 3520, "10PV14-1780" → 1780
    if result.rated_speed == 0 and result.curve_number:
        import re
        cn = result.curve_number.strip()
        # Match a 3-4 digit number after a dash at the end
        m = re.search(r'-(\d{3,4})$', cn)
        if m:
            result.rated_speed = int(m.group(1))
            print(f"Speed from curve number '{cn}': "
                  f"{result.rated_speed}")

    # ── Fallback 3: Derive from poles and frequency ──────────
    #    RPM = 120 × Hz / poles  (synchronous speed)
    if result.rated_speed == 0 and result.poles > 0 and result.frequency > 0:
        result.rated_speed = 120 * result.frequency / result.poles
        print(f"Speed from poles/Hz: {result.poles}P {result.frequency}Hz "
              f"→ {result.rated_speed} RPM")

    if curve_sheet_name is None:
        return result

    # ── Bulk-read the entire curve sheet with pandas ─────────
    # header=None gives us raw cell values; no column parsing.
    # This is MUCH faster than cell-by-cell openpyxl access.
    try:
        raw = pd.read_excel(filepath, sheet_name=curve_sheet_name,
                            header=None, engine="openpyxl")
    except Exception as e:
        print(f"Error reading curve sheet '{curve_sheet_name}': {e}")
        return result

    # Convert to a simple 2D accessor.  raw.iloc[row, col] where
    # row 0 = Excel row 1, col 0 = Excel col A.
    def cell(row_1based, col_1based):
        """Access like openpyxl: 1-based row and column."""
        r = row_1based - 1
        c = col_1based - 1
        if r < 0 or r >= len(raw) or c < 0 or c >= len(raw.columns):
            return None
        val = raw.iloc[r, c]
        return None if pd.isna(val) else val

    # ── Read diameters from column A, rows 10+ ───────────────
    diameters = []
    for row in range(DATA_START_ROW, DATA_END_ROW + 1):
        val = _num(cell(row, 1)) # Impeller diameter in column A
        hub_side_val = _num(cell(row, 2)) # Hub side diameter in column B
        if hub_side_val is not None and hub_side_val > 0 and val is not None and val > 0:
            diameter = (val + hub_side_val) / 2
            diameters.append(diameter)
        elif val is not None and val > 0:
            diameters.append(val)
        else:
            # Stop reading when we hit a blank or non-numeric cell
            break
    if not diameters:
        return result

    # ── Read curve data for each diameter ────────────────────
    for idx, dia in enumerate(diameters):
        block_start = BLOCK_START_COL + (idx * BLOCK_WIDTH)

        # Verify diameter in row 7
        check_dia = _num(cell(DIAMETER_ROW, block_start))
        if check_dia is not None and abs(check_dia - dia) > 0.01:
            # Scan row 7 for the correct block
            found = False
            for scan_col in range(BLOCK_START_COL,
                                  BLOCK_START_COL + len(diameters) * BLOCK_WIDTH,
                                  BLOCK_WIDTH):
                scan_dia = _num(cell(DIAMETER_ROW, scan_col))
                if scan_dia is not None and abs(scan_dia - dia) < 0.01:
                    block_start = scan_col
                    found = True
                    break
            if not found:
                continue

        trim = TrimCurve(diameter=round(dia, 4))

        # Head curve
        trim.head_flow, trim.head = _read_xy(
            cell, block_start + HEAD_FLOW_OFFSET,
            block_start + HEAD_VAL_OFFSET,
        )

        # Power curve
        trim.power_flow, trim.power = _read_xy(
            cell, block_start + POWER_FLOW_OFFSET,
            block_start + POWER_VAL_OFFSET,
        )

        # NPSH curve
        trim.npsh_flow, trim.npsh = _read_xy(
            cell, block_start + NPSH_FLOW_OFFSET,
            block_start + NPSH_VAL_OFFSET,
        )

        # BEP metadata from row 7
        trim.bep_flow = _num(cell(7, block_start + BEP_FLOW_OFFSET))
        trim.bep_efficiency = _num(cell(7, block_start + BEP_EFF_OFFSET))
        trim.shutoff_power = _num(cell(7, block_start + SHUTOFF_PWR_OFFSET))

        result.trims.append(trim)

    return result


def _read_xy(cell_fn, x_col, y_col,
             start_row=DATA_START_ROW, end_row=DATA_END_ROW):
    """
    Read paired X/Y spline knot data using the cell accessor function.

    PX curve sheets have actual data in rows 10-30ish, then blank
    rows, then a metric conversion section below. We stop reading
    when we encounter consecutive blank rows after data has started.
    """
    xs, ys = [], []
    consecutive_blanks = 0

    for row in range(start_row, end_row + 1):
        x = _num(cell_fn(row, x_col))
        y = _num(cell_fn(row, y_col))

        if x is None or y is None:
            if xs:
                consecutive_blanks += 1
                # Stop after 3+ consecutive blank rows — we've
                # left the data section
                if consecutive_blanks >= 3:
                    break
            continue

        # Reset blank counter on valid data
        consecutive_blanks = 0

        # Skip leading (0, 0) before real data
        if x == 0 and y == 0 and not xs:
            continue

        # Stop at (0, 0) after data — section boundary
        if x == 0 and y == 0 and xs:
            break

        xs.append(x)
        ys.append(y)

    if not xs:
        return None, None

    return np.array(xs), np.array(ys)


def _num(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None