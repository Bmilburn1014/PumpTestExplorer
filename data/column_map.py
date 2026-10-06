# data/column_map.py
#
# Normalizes columns from different index file formats into a
# standard schema. This lets the rest of the app use consistent
# field names (e.g., "test_id", "rated_flow") regardless of which
# index file the data came from.

import pandas as pd
from config import INDEX_FILES


def normalize_index_df(df: pd.DataFrame, index_name: str) -> pd.DataFrame:
    """
    Rename columns from the raw Excel headers to standard field names.

    Args:
        df:         Raw DataFrame read from an index file
        index_name: Name of the index file config (e.g., "new_test_list")

    Returns:
        DataFrame with standardized column names. Original columns that
        don't have a mapping are preserved as-is.
    """
    # Find the column mapping for this index file
    col_map = None
    for cfg in INDEX_FILES:
        if cfg["name"] == index_name:
            col_map = cfg.get("columns", {})
            break

    if not col_map:
        return df

    # Strip whitespace from actual DataFrame column names first
    df.columns = [str(c).strip() if isinstance(c, str) else c
                  for c in df.columns]

    # Build lookup: lowercased-stripped actual col → actual col
    actual_lower = {str(c).strip().lower(): c for c in df.columns}

    # Invert: { actual_col → standard_name }
    # Try exact match first, then stripped, then case-insensitive
    rename_map = {}
    for std_name, excel_header in col_map.items():
        if excel_header in df.columns:
            rename_map[excel_header] = std_name
        elif excel_header.strip() in df.columns:
            rename_map[excel_header.strip()] = std_name
        elif excel_header.strip().lower() in actual_lower:
            actual_col = actual_lower[excel_header.strip().lower()]
            rename_map[actual_col] = std_name

    return df.rename(columns=rename_map)


def get_column_mapping(index_name: str) -> dict:
    """
    Return the standard_name → Excel column mapping for an index file.

    Args:
        index_name: Name of the index file config

    Returns:
        Dict like {"test_id": "Test ID", "rated_flow": "Q (GPM)", ...}
    """
    for cfg in INDEX_FILES:
        if cfg["name"] == index_name:
            return cfg.get("columns", {})
    return {}


def get_original_column(index_name: str, standard_name: str) -> str | None:
    """
    Look up the original Excel column name for a standard field.

    Args:
        index_name:    Name of the index file config
        standard_name: Standard field name (e.g., "test_id")

    Returns:
        The original Excel column name, or None if not mapped.
    """
    mapping = get_column_mapping(index_name)
    return mapping.get(standard_name)


# ── Standard fields that both index files share ──────────────
# These are the fields the app can reliably use across all sources.
COMMON_FIELDS = [
    "test_id",
    "job_number",
    "pump_model",
    "rated_flow",
    "rated_head",
    "rpm",
    "pass_fail",
    "fire_pump",
    "test_date",
    "test_run",
]


def get_common_fields_available(df: pd.DataFrame) -> list[str]:
    """Return which common fields exist in a normalized DataFrame."""
    return [f for f in COMMON_FIELDS if f in df.columns]