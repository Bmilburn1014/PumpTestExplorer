# data/sync.py
#
# Pulls data from Excel files into the local SQLite cache.
# Index files: read with pandas, columns normalized.
# Detail files: read with excel_reader (handles duplicate headers).

import pandas as pd
from data.cache_db import CacheDB
from data.column_map import normalize_index_df
from data.excel_reader import read_detail_file
from data.path_resolver import build_detail_path
from config import INDEX_FILES


def _normalize_date_column(series: pd.Series) -> pd.Series:
    """
    Convert a date column to ISO-format strings (YYYY-MM-DD).

    Handles:
      - Python datetime objects (from openpyxl)
      - Excel serial numbers (float days since 1899-12-30)
      - Various string formats (MM/DD/YYYY, etc.)
      - NaT / NaN → empty string
    """
    # Step 1: Try pd.to_datetime on the raw values.
    # openpyxl already gives us datetime objects for date-formatted
    # cells, so this usually works on the first pass.
    parsed = pd.to_datetime(series, format = "%m/%d/%y", errors="coerce")

    # Step 2: If most values failed, the column might contain
    # Excel serial numbers stored as floats/ints.
    nat_pct = parsed.isna().sum() / max(len(parsed), 1)
    if nat_pct > 0.5:
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().sum() > parsed.notna().sum():
            serial_parsed = pd.to_datetime(
                numeric, unit="D", origin="1899-12-30", errors="coerce"
            )
            # Use serial parse where it worked and original didn't
            fill_mask = parsed.isna() & serial_parsed.notna()
            parsed[fill_mask] = serial_parsed[fill_mask]

    # Step 3: Try common explicit string formats for still-missing values
    if parsed.isna().any():
        remaining = series[parsed.isna()]
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y",
                    "%m/%d/%y", "%d-%b-%Y",
                    "%m/%d/%Y %H:%M:%S", "%Y/%m/%d"):
            try:
                attempt = pd.to_datetime(remaining, format=fmt,
                                         errors="coerce")
                fill_mask = parsed.isna() & attempt.notna()
                parsed[fill_mask] = attempt[fill_mask]
                remaining = series[parsed.isna()]
                if remaining.empty:
                    break
            except Exception:
                pass

    # Step 4: Convert to YYYY-MM-DD strings, NaT → ""
    return parsed.dt.strftime("%Y-%m-%d").fillna("")

def _detect_header_row(path, sheet=0, max_scan=10):
    """
    Scan the first few rows of an Excel sheet to find the actual
    header row.  Returns the 0-based row index.

    Heuristic: the header row is the first row where at least 4
    cells are non-empty strings and fewer than half look like
    'Unnamed' or are numbers.
    """
    try:
        preview = pd.read_excel(
            path, sheet_name=sheet, header=None,
            nrows=max_scan, engine="openpyxl",
        )
    except Exception:
        return 0

    for i in range(min(max_scan, len(preview))):
        row_vals = preview.iloc[i].dropna().astype(str).tolist()
        if len(row_vals) < 4:
            continue
        # Count values that look like real column names
        named = [v for v in row_vals
                 if len(v) > 1
                 and not v.startswith("Unnamed")
                 and not v.replace(".", "", 1).isdigit()]
        if len(named) >= 4:
            return i

    return 0  # fallback

class SyncEngine:

    def __init__(self):
        self.cache = CacheDB()

    def sync_index_files(self, progress_callback=None):
        """Read all index files into local cache with normalized columns."""
        for i, cfg in enumerate(INDEX_FILES):
            name = cfg["name"]
            path = cfg["path"]
            sheet = cfg.get("sheet", 0)

            if progress_callback:
                progress_callback(
                    step=f"Loading index: {name}",
                    progress=(i / len(INDEX_FILES)) * 50,
                )

            try:
                # Auto-detect the header row (some index files have
                # a title row before the actual column headers)
                header_row = _detect_header_row(path, sheet)
                df = pd.read_excel(
                    path, sheet_name=sheet, header=header_row,
                    engine="openpyxl",
                )
                if header_row > 0:
                    print(f"  Index '{name}': header detected at row {header_row}")
                df = normalize_index_df(df, name)
                df["_source_index"] = name

                # Normalize date columns to ISO strings so they
                # survive the SQLite text roundtrip cleanly.
                for col in ("test_date",):
                    if col in df.columns:
                        df[col] = _normalize_date_column(df[col])

                self.cache.store_dataframe(
                    f"index_{name}", df, source_path=str(path)
                )
                print(f"Synced index '{name}': {len(df)} rows")
            except FileNotFoundError:
                print(f"WARNING: Index file not found: {path}")
            except Exception as e:
                print(f"WARNING: Failed to read index {path}: {e}")

        if progress_callback:
            progress_callback(step="Index files cached", progress=50)

    def sync_detail_file(self, test_id: str,
                         index_name: str = None,
                         row_info: dict = None) -> bool:
        """
        Sync a single detail file by test_id.
        Uses excel_reader to handle the TEST DATA sheet with
        duplicate column headers.

        Args:
            test_id:    Test identifier
            index_name: Which index file (for path resolution)
            row_info:   Dict with test_date, etc. for year folder lookup
        """
        path = build_detail_path(test_id, row=row_info,
                                 index_name=index_name)
        if path is None:
            print(f"WARNING: Could not resolve path for {test_id}")
            return False

        if not path.exists():
            print(f"WARNING: File not found: {path}")
            return False

        try:
            df = read_detail_file(str(path))
            if df.empty:
                print(f"WARNING: No data read from {path}")
                return False

            self.cache.store_dataframe(
                f"detail_{test_id}", df, source_path=str(path)
            )
            print(f"Synced detail '{test_id}': {len(df)} rows "
                  f"from {path}")
            return True
        except Exception as e:
            print(f"WARNING: Failed to sync detail for {test_id}: {e}")
            return False

    def sync_all_detail_files(self, progress_callback=None):
        """Sync all detail files referenced by the index data."""
        # Get all test_ids from cached index tables
        test_ids = []
        for cfg in INDEX_FILES:
            table = f"index_{cfg['name']}"
            if self.cache.table_exists(table):
                df = self.cache.read_table(table)
                if "test_id" in df.columns:
                    ids = df["test_id"].dropna().astype(str).unique().tolist()
                    test_ids.extend(ids)

        total = len(test_ids)
        synced = 0
        for i, tid in enumerate(test_ids):
            if progress_callback:
                pct = 50 + (i / max(total, 1)) * 50
                progress_callback(step=f"Loading: {tid}", progress=pct)

            if self.sync_detail_file(tid):
                synced += 1

        if progress_callback:
            progress_callback(
                step=f"Sync complete ({synced}/{total})", progress=100
            )

    def needs_sync(self) -> bool:
        """Check if the cache has any index data."""
        for cfg in INDEX_FILES:
            if self.cache.table_exists(f"index_{cfg['name']}"):
                return False
        return True

    def get_sync_status(self) -> list[dict]:
        """Return metadata for all cached tables."""
        return self.cache.list_cached_tables()