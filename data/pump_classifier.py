# data/pump_classifier.py
#
# Classifies pump models using regex patterns and routes to the
# correct index file for data lookup.
#
# Classification rules:
#
#   INLINE PUMP:
#     Model contains: {number}PV{number} or {number}PVF{number}
#     Pattern: \d+\.?\d*PVF?\d+
#     Examples: 2.5PVF8, 10PV14, 8PV12A
#     Index: PPU Dailey Hor Inl Std Test Log Rotation
#     Trim column: "material_2" (MATL 2 in raw Excel = impeller trim dia)
#
#   HORIZONTAL PUMP:
#     Model format: {digits}{AE|AEF|TU|TUT}{digits}
#     AE and AEF are one family. TU and TUT are different models.
#     Examples: 4AE12, 4AEF12, 4TU12, 4TUT12
#     Index: PPU Dailey Hor Inl Std Test Log Rotation
#     Trim column: "material_2"
#
#   END SUCTION PUMP:
#     Model format: {F|C}{3-4 digits}
#     Pattern: [FC]\d{3,4}
#     Examples: F1020, C1020, C820, F820
#     Index: PPU Dailey Hor Inl Std Test Log Rotation
#     Trim column: "material_2"
#
#   VERTICAL TURBINE (VT):
#     Model format: [letter]##AAA##-#########
#       - Optional leading letter prefix (e.g., "A")
#       - 1-2 digit bowl diameter (e.g., 8, 12)
#       - 2-4 letter bowl model name (e.g., MD, HXB)
#       - 1-2 digit capacity marking (e.g., 14)
#       - Dash then impeller part number (e.g., 123456789)
#     Examples: 8MD12-601234567, A12HXB14-504291814
#     Index: NEWTESTLISTv.2  (same as generic vertical)
#     Trim column: "impeller_trim"
#     Extra: bowl_diameter and impeller_part_number used for filtering
#
#   VERTICAL PUMP (default):
#     Everything else
#     Index: NEWTESTLISTv.2
#     Trim column: "impeller_trim" or "bowl_size"

import re
from dataclasses import dataclass


# -- Regex patterns ----------------------------------------------------

# Inline: digits (with optional decimal), then PV or PVF, then digits
# (optionally followed by a letter), then optionally -digits for speed
INLINE_PATTERN = re.compile(
    r'\d+\.?\d*PVF?\d+(?:-\d+)([A-Za-z]?)?',
    re.IGNORECASE,
)

# Horizontal: digits, then AE/AEF/TU/TUT, then digits.
# Longest code first so TUT is not read as TU, and AEF is not read as AE.
# AE and AEF are the same family. TU and TUT are different models.
HORIZONTAL_PATTERN = re.compile(
    # AES is treated as AE: an extra letter after AE is a typo, not vertical.
    r'^\s*(?:[A-Za-z]\s*)?(\d+)\s*(AEF|AE[A-Za-z]*|TUT|TU)\s*(\d+)'
    r'\s*([A-Za-z]\d*)?(?:\s*-\s*(\d+))?\s*$',
    re.IGNORECASE,
)
# Same token anywhere in the string, so extra curve text cannot fall to vertical.
HORIZONTAL_FIND = re.compile(
    r'(\d+)\s*(AEF|AE[A-Za-z]*|TUT|TU)\s*(\d+)'
    r'\s*([A-Za-z]\d*)?(?:\s*-\s*(\d+))?',
    re.IGNORECASE,
)

# End suction: F or C followed by 3-4 digits, optionally -digits for speed
END_SUCTION_PATTERN = re.compile(
    r'^([FC])(\d{3,4})(?:-(\d+))?$',
    re.IGNORECASE,
)

# Vertical pump:
#   [optional letter] + 1-2 digit bowl + 2+ letters + optional digits +
#   optional dash-suffix (all-digit = impeller part, otherwise variant)
VERTICAL_PATTERN = re.compile(
    r'^(?P<prefix>[A-Za-z])?'
    r'(?P<bowl>\d{1,2})'
    # Horizontal families are not vertical models.
    r'(?P<letters>(?!(?:AEF|AE|TUT|TU))[A-Za-z]+)'
    r'(?P<trailing>\d{0,2}[A-Za-z]?)'
    r'(?:-(?P<suffix>[A-Za-z0-9]+))?$',
    re.IGNORECASE,
)


@dataclass
class PumpClassification:
    """Result of classifying a pump model."""
    pump_type: str           # "inline", "horizontal", "end_suction", "vertical", "unknown"
    model_match: str         # The matched portion of the model string
    index_name: str          # Config index file name to use
    trim_column: str         # Which normalized column holds the trim diameter
    raw_model: str           # The original input string
    speed_from_model: int | None = None   # Speed extracted from model if present
    bowl_diameter: int | None = None      # VT: bowl size for index filtering
    impeller_part: str | None = None      # VT: part number for index filtering
    capacity: int | None = None           # VT: capacity marking
    base_model_name: str | None = None    # VT: base model letters (e.g. "AES")
    perf_suffix: str | None = None        # VT: performance suffix (e.g. "J")
    detail_path: str | None = None        # VT: detail subfolder (e.g. "14HXB")
    discharge_size: float | None = None   # Horizontal/End-suction: discharge size
    series_letter: str | None = None       # End-suction: F or C series identifier
    test_iteration_column: str | None = None  #number of tests for test iteration filtering (e.g., "Test No..1" in PPU test log)


# -- Known base model names (used to split performance suffixes) --------
# Models like "AESJ" = base "AES" + perf suffix "J".
# Add new base names here as they appear in the test index.
_KNOWN_BASE_MODELS = {"MA", "HXB", "GLME", "HH", "HHOH", "MB", "MD", "MC", "HXC", "LA", "LB", "LC", "LD"}

# -- Known horizontal model codes (used to disambiguate from VT) --------
_HORIZONTAL_CODES = {"AE", "AEF", "TU", "TUT"}


def _normalize_model(text: str) -> str:
    """Make curve names comparable. Excel often uses odd dash characters."""
    text = str(text or "").strip()
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212", "\u00ad"):
        text = text.replace(dash, "-")
    text = text.replace("/", "-").replace("\\", "")
    return " ".join(text.split())


def _horizontal_code(code: str) -> str:
    """Map a matched code to its family. AES and similar AE typos are AE."""
    code = (code or "").upper()
    if code.startswith("AEF"):
        return "AEF"
    if code.startswith("AE"):
        return "AE"
    if code.startswith("TUT"):
        return "TUT"
    if code.startswith("TU"):
        return "TU"
    return code


def _strip_perf_suffix(letters: str) -> tuple[str, str]:
    """
    Split a VT model letter string into (base_model, perf_suffix).

    Tries longest known base model match first.  If none match, treats
    the full string as the base model with no suffix.

    Examples:
        "AESJ"  -> ("AES", "J")
        "AES"   -> ("AES", "")
        "MD"    -> ("MD",  "")
        "XYZ"   -> ("XYZ", "")   # unknown model, kept as-is
    """
    # Try longest match first so "MDA" beats "MD"
    for length in range(len(letters), 0, -1):
        candidate = letters[:length].upper()
        if candidate in _KNOWN_BASE_MODELS:
            return candidate, letters[length:]
    return letters, ""


def classify_pump(model_or_curve: str) -> PumpClassification:
    """
    Classify a pump by its model name or PX curve number.

    Tries patterns in order of specificity:
      1. Inline (PV/PVF pattern)
      2. Horizontal (AE/AEF/TU/TUT pattern)
      3. End suction (F### or C### pattern)
      4. Vertical Turbine (bowl + model + capacity - part number)
      5. Default vertical

    Args:
        model_or_curve: e.g., "2.5PVF8-3520", "A12AES14-504291814",
                        "4AE12", "F1020", "10AE14J", "MD"

    Returns:
        PumpClassification with routing info
    """
    text = _normalize_model(model_or_curve)

    # -- 1. Try inline pump pattern first (most specific) --------------
    match = INLINE_PATTERN.search(text)
    if match:
        matched = match.group()

        # Try to extract speed from the model (e.g., "2.5PVF8-3520")
        speed = None
        speed_match = re.search(r'-(\d{3,4})$', text)
        if speed_match:
            speed = int(speed_match.group(1))

        return PumpClassification(
            pump_type="inline",
            model_match=matched,
            index_name="ppu_test_log",
            trim_column="material_2",
            raw_model=text,
            speed_from_model=speed,
            test_iteration_column="Test No..1"
        )

    # -- 2. Try horizontal pump pattern --------------------------------
    match = HORIZONTAL_PATTERN.match(text) or HORIZONTAL_FIND.search(text)
    if match:
        discharge = match.group(1)
        model_code = _horizontal_code(match.group(2))
        casing = match.group(3)
        variant = match.group(4) or ""
        speed_str = match.group(5)
        speed = int(speed_str) if speed_str else None

        return PumpClassification(
            pump_type="horizontal",
            model_match=text,
            index_name="ppu_test_log",
            trim_column="material_2",
            raw_model=text,
            speed_from_model=speed,
            discharge_size=float(discharge),
            base_model_name=model_code,
            bowl_diameter=int(casing),
            test_iteration_column="Test No..1"
        )

    # -- 3. Try end suction pump pattern -------------------------------
    match = END_SUCTION_PATTERN.match(text)
    if match:
        series = match.group(1).upper()
        model_num = match.group(2)
        speed_str = match.group(3)
        speed = int(speed_str) if speed_str else None

        return PumpClassification(
            pump_type="end_suction",
            model_match=text,
            index_name="ppu_test_log",
            trim_column="material_2",
            raw_model=text,
            speed_from_model=speed,
            series_letter=series,
            base_model_name=f"{series}{model_num}",
            test_iteration_column="Test No..1"
        )

    # -- 4. Vertical pump -----------------------------------------------
    match = VERTICAL_PATTERN.match(text)
    if match:
        # Guard: if the letter code is a horizontal code, don't treat
        # as vertical.  This shouldn't happen because HORIZONTAL_PATTERN
        # fires first, but protects against edge cases with prefixes.
        letters = match.group("letters")
        if letters.upper() in _HORIZONTAL_CODES:
            # Re-route as horizontal (prefix letter treated as variant)
            bowl = int(match.group("bowl"))
            trailing = match.group("trailing") or ""
            cap_match = re.match(r'(\d+)', trailing)
            casing = int(cap_match.group(1)) if cap_match else None
            return PumpClassification(
                pump_type="horizontal",
                model_match=text,
                index_name="ppu_test_log",
                trim_column="material_2",
                raw_model=text,
                discharge_size=float(bowl),
                base_model_name=letters.upper(),
                bowl_diameter=casing,
            )

        bowl = int(match.group("bowl"))
        base_model, perf = _strip_perf_suffix(letters)

        # Extract capacity from trailing digits (e.g. "14" in "12AES14")
        trailing = match.group("trailing") or ""
        cap_match = re.match(r'(\d+)', trailing)
        capacity = int(cap_match.group(1)) if cap_match else None

        # Dash-suffix: all digits (4+) = impeller part number,
        # otherwise it's a variant code (ignored for filtering)
        dash_suffix = match.group("suffix") or ""
        impeller_part = None
        if dash_suffix.isdigit() and len(dash_suffix) >= 4:
            impeller_part = dash_suffix

        # Prefix: Optional Letter prefix (e.g., "M" in M12HXB)
        # Both classics and modulars are searched in the index.
        prefix = match.group("prefix") or ""

        return PumpClassification(
            pump_type="vertical",
            model_match=text,
            index_name="new_test_list",
            trim_column="impeller_trim",
            raw_model=text,
            bowl_diameter=bowl,
            impeller_part=impeller_part,
            capacity=capacity,
            base_model_name=base_model,
            perf_suffix=perf,
            detail_path=f"{bowl}{base_model}",
        )

    # -- 5. Horizontal token anywhere beats the vertical fallback ------
    match = HORIZONTAL_FIND.search(text)
    if match:
        return PumpClassification(
            pump_type="horizontal",
            model_match=match.group(0),
            index_name="ppu_test_log",
            trim_column="material_2",
            raw_model=text,
            speed_from_model=int(match.group(5)) if match.group(5) else None,
            discharge_size=float(match.group(1)),
            base_model_name=_horizontal_code(match.group(2)),
            bowl_diameter=int(match.group(3)),
            test_iteration_column="Test No..1",
        )

    # -- 6. Fallback: generic vertical ----------------------------------
    return PumpClassification(
        pump_type="vertical",
        model_match=text,
        index_name="new_test_list",
        trim_column="impeller_trim",
        raw_model=text,
    )


def extract_pump_size(model_or_curve: str) -> dict:
    """
    Extract pump sizing info from the model/curve string.

    For inline: "2.5PVF8-3520"
        -> discharge_size=2.5, bowl_or_casing=8, speed=3520

    For horizontal: "4AE12"
        -> discharge_size=4, bowl_or_casing=12, model_code="AE"

    For end suction: "F1020"
        -> discharge_size=None, series="F", model_number="1020"

    For VT: "A12AES14-504291814"
        -> discharge_size=None, bowl_or_casing=12, model_name="AES",
           capacity=14, impeller_part="504291814"

    For curve numbers like "10PV14-1780"
        -> discharge_size=10, bowl_or_casing=14, speed=1780
    """
    text = _normalize_model(model_or_curve)

    # Try inline pattern first
    m = re.match(
        r'(\d+\.?\d*)PVF?(\d+)([A-Za-z]?)(?:-(\d+))?',
        text, re.IGNORECASE,
    )
    if m:
        return {
            "discharge_size": float(m.group(1)),
            "bowl_or_casing": int(m.group(2)),
            "model_name": None,
            "model_code": None,
            "series": None,
            "variant": m.group(3) or "",
            "capacity": None,
            "impeller_part": None,
            "speed": int(m.group(4)) if m.group(4) else None,
        }

    # Try horizontal pattern, including extra curve text
    m = HORIZONTAL_PATTERN.match(text) or HORIZONTAL_FIND.search(text)
    if m:
        return {
            "discharge_size": float(m.group(1)),
            "bowl_or_casing": int(m.group(3)),
            "model_name": None,
            "model_code": _horizontal_code(m.group(2)),
            "series": None,
            "variant": m.group(4) or "",
            "capacity": None,
            "impeller_part": None,
            "speed": int(m.group(5)) if m.group(5) else None,
        }

    # Try end suction pattern
    m = END_SUCTION_PATTERN.match(text)
    if m:
        return {
            "discharge_size": None,
            "bowl_or_casing": None,
            "model_name": None,
            "model_code": None,
            "series": m.group(1).upper(),
            "model_number": m.group(2),
            "variant": "",
            "capacity": None,
            "impeller_part": None,
            "speed": int(m.group(3)) if m.group(3) else None,
        }

    # Try VT pattern
    m = VERTICAL_PATTERN.match(text)
    if m:
        # Check for horizontal codes caught by VT pattern
        letters = m.group("letters")
        if letters.upper() in _HORIZONTAL_CODES:
            trailing = m.group("trailing") or ""
            cap_match = re.match(r'(\d+)', trailing)
            casing = int(cap_match.group(1)) if cap_match else None
            return {
                "discharge_size": float(m.group("bowl")),
                "bowl_or_casing": casing,
                "model_name": None,
                "model_code": letters.upper(),
                "series": None,
                "variant": m.group("prefix") or "",
                "capacity": None,
                "impeller_part": None,
                "speed": None,
            }

        # Extract capacity from trailing digits (e.g. "14" in "12AES14")
        trailing = m.group("trailing") or ""
        cap_match = re.match(r'(\d+)', trailing)
        capacity = int(cap_match.group(1)) if cap_match else None

        # Dash-suffix: all digits (4+) = impeller part number
        suffix = m.group("suffix") or ""
        impeller_part = suffix if suffix.isdigit() and len(suffix) >= 4 else None

        # Split letters into base model + performance suffix
        base_model, perf = _strip_perf_suffix(m.group("letters"))

        return {
            "discharge_size": None,
            "bowl_or_casing": int(m.group("bowl")),
            "model_name": base_model,
            "model_code": None,
            "series": None,
            "variant": m.group("prefix") or "",
            "capacity": capacity,
            "impeller_part": impeller_part,
            "speed": None,
            "perf_suffix": perf,
        }

    return {
        "discharge_size": None, "bowl_or_casing": None,
        "model_name": None, "model_code": None, "series": None,
        "variant": "", "capacity": None,
        "impeller_part": None, "speed": None,
    }


def build_model_search_pattern(curve_number: str) -> str:
    """
    Build a regex search pattern for finding matching tests in the index.

    For inline "2.5PVF8-3520":
        Matches "2.5PVF8", "2.5PV8", etc.

    For horizontal "4AE12" or "4AEF12":
        Matches both "4AE12" and "4AEF12".
        "4TU12" matches only TU. "4TUT12" matches only TUT.

    For end suction "F1020":
        Matches "F1020"

    For VT "A12AES14-504291814":
        Matches bowl size 12 + model AES, e.g. "12AES", "A12AES"
        The impeller part number is filtered separately by
        comparison_engine, not embedded in this regex.

    Returns a string suitable for pandas .str.contains().
    """
    info = extract_pump_size(curve_number)

    # Inline pump
    if info["discharge_size"] is not None and info.get("model_code") is None:
        discharge = str(info["discharge_size"])
        if discharge.endswith(".0"):
            discharge = discharge[:-2]
        casing = str(info["bowl_or_casing"])
        return rf'{re.escape(discharge)}\.?0?PVF?{re.escape(casing)}'

    # Horizontal pump
    if info.get("model_code") in _HORIZONTAL_CODES:
        discharge = str(int(info["discharge_size"])) if info["discharge_size"] else ""
        model_code = re.escape(info["model_code"])
        casing = str(info["bowl_or_casing"]) if info["bowl_or_casing"] else ""
        variant = info.get("variant", "")
        # AE and AEF are the same family. TU must not match TUT.
        code = info["model_code"].upper()
        if code in ("AE", "AEF"):
            family = r"AEF?"
        elif code == "TU":
            family = r"TU(?!T)"
        elif code == "TUT":
            family = r"TUT(?![A-Za-z])"
        else:
            family = model_code
        # A leading letter is a prefix, not part of the model to require.
        # Allow an optional variant letter and speed suffix in the index.
        return (
            rf'^\s*[A-Za-z]?\s*{re.escape(discharge)}\s*{family}\s*'
            rf'{re.escape(casing)}\s*(?:[A-Za-z]\d*)?\s*(?:-\s*\d+)?\s*$'
        )

    # End suction pump
    if info.get("series") is not None:
        series = re.escape(info["series"])
        model_num = re.escape(info.get("model_number", ""))
        return rf'^\s*{series}{model_num}\s*$'

    # Vertical turbine
    if info.get("model_name") is not None:
        model_name = re.escape(info["model_name"])
        return rf'^\s*[A-Za-z]?{model_name}[A-Za-z]?\s*$'

    found = HORIZONTAL_FIND.search(_normalize_model(curve_number))
    if found:
        discharge = found.group(1)
        family_code = _horizontal_code(found.group(2))
        casing = found.group(3)
        if family_code in ("AE", "AEF"):
            family = r"AEF?"
        elif family_code == "TU":
            family = r"TU(?!T)"
        elif family_code == "TUT":
            family = r"TUT(?![A-Za-z])"
        else:
            family = re.escape(family_code)
        return (
            rf'^\s*[A-Za-z]?\s*{re.escape(discharge)}\s*{family}\s*'
            rf'{re.escape(casing)}\s*(?:[A-Za-z]\d*)?\s*(?:-\s*\d+)?\s*$'
        )

    # Fallback: literal match
    return re.escape(curve_number)