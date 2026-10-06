# data/loader.py
#
# Main data access layer. All reads go through the local SQLite cache.
# Auto-syncs index files on first use.

import pandas as pd
from data.cache_db import CacheDB
from data.sync import SyncEngine
from config import INDEX_FILES


class DataSource:

    def __init__(self):
        self.cache = CacheDB()
        self.sync = SyncEngine()

        # Auto-sync index files if cache is empty
        if self.sync.needs_sync():
            self.sync.sync_index_files()

    # ── Index Data ───────────────────────────────────────────

    def get_index_data(self, index_name: str = None,
                       filters: dict = None) -> pd.DataFrame:
        """
        Read index data from cache.
        If index_name is None, returns combined data from all sources.
        Columns are normalized to standard field names.
        """
        if index_name:
            table = f"index_{index_name}"
            if self.cache.table_exists(table):
                return self.cache.read_table(table, filters)
            return pd.DataFrame()

        frames = []
        for cfg in INDEX_FILES:
            table = f"index_{cfg['name']}"
            if self.cache.table_exists(table):
                df = self.cache.read_table(table, filters)
                frames.append(df)

        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def search_index(self, search_text: str = None,
                     pump_model: str = None,
                     bowl_size: float = None,
                     job_number: str = None,
                     pass_fail: str = None) -> pd.DataFrame:
        """Search across all index data with optional filters."""
        df = self.get_index_data()
        if df.empty:
            return df

        if search_text:
            text = search_text.lower()
            mask = pd.Series(False, index=df.index)
            for col in df.select_dtypes(include=["object"]).columns:
                mask |= df[col].astype(str).str.lower().str.contains(
                    text, na=False
                )
            df = df[mask]

        if pump_model and "pump_model" in df.columns:
            df = df[df["pump_model"].astype(str).str.contains(
                pump_model, case=False, na=False
            )]

        if bowl_size is not None and "bowl_size" in df.columns:
            df = df[df["bowl_size"] == bowl_size]

        if job_number and "job_number" in df.columns:
            df = df[df["job_number"].astype(str).str.contains(
                str(job_number), na=False
            )]

        if pass_fail and "pass_fail" in df.columns:
            df = df[df["pass_fail"].astype(str).str.lower().str.contains(
                pass_fail.lower(), na=False
            )]

        return df

    # ── Detail Data ──────────────────────────────────────────

    def get_detail_data(self, test_id: str,
                        filters: dict = None,
                        index_name: str = None,
                        row_info: dict = None) -> pd.DataFrame:
        """
        Read detail data from cache. Auto-syncs if not cached.

        Args:
            test_id:    Test identifier
            filters:    Optional SQL-level filters
            index_name: Which index file (for path resolution)
            row_info:   Dict with test_date, etc. for finding year folder

        Returns DataFrame with standardized columns:
            flow, speed, tdh_bowl, tdh_pump,
            power_bowl_motor, power_bowl_dyno,
            power_pump_motor, power_pump_dyno,
            eff_bowl_motor, eff_bowl_dyno,
            eff_pump_motor, eff_pump_dyno,
            eff_overall, npsha, lift, curve_no
        """
        table = f"detail_{test_id}"
        if not self.cache.table_exists(table):
            self.sync.sync_detail_file(
                test_id,
                index_name=index_name,
                row_info=row_info,
            )
        if self.cache.table_exists(table):
            return self.cache.read_table(table, filters)
        return pd.DataFrame()

    def is_detail_cached(self, test_id: str) -> bool:
        """Check if a detail file is already in the cache."""
        return self.cache.table_exists(f"detail_{test_id}")

    # ── Dropdown Options ─────────────────────────────────────

    def get_dropdown_options(self) -> list[dict]:
        """
        Build dropdown options for the dataset selector.
        Label format: test_id | model | bowl_size | flow @ head | [P/F]
        """
        df = self.get_index_data()
        if df.empty or "test_id" not in df.columns:
            return []

        options = []
        for _, row in df.iterrows():
            test_id = str(row.get("test_id", "")).strip()
            if not test_id or test_id == "nan":
                continue

            parts = [test_id]

            model = row.get("pump_model")
            if pd.notna(model):
                parts.append(str(model))

            bowl = row.get("bowl_size")
            if pd.notna(bowl):
                parts.append(f'{bowl}"')

            flow = row.get("rated_flow")
            head = row.get("rated_head")
            if pd.notna(flow) and pd.notna(head):
                parts.append(f"{flow} GPM @ {head} ft")

            pf = row.get("pass_fail")
            if pd.notna(pf):
                parts.append(f"[{pf}]")

            label = " | ".join(parts)
            options.append({"label": label, "value": test_id})

        return options

    def get_test_summary(self, test_id: str) -> dict | None:
        """Quick lookup of a test's index data."""
        df = self.get_index_data()
        if "test_id" not in df.columns:
            return None

        match = df[df["test_id"].astype(str) == str(test_id)]
        if match.empty:
            return None

        row = match.iloc[0]
        return {
            "test_id": str(row.get("test_id", "")),
            "job_number": str(row.get("job_number", "")),
            "pump_model": str(row.get("pump_model", "")),
            "bowl_size": row.get("bowl_size"),
            "rated_flow": row.get("rated_flow"),
            "rated_head": row.get("rated_head"),
            "rpm": row.get("rpm"),
            "pass_fail": str(row.get("pass_fail", "")),
            "source_index": str(row.get("_source_index", "")),
        }

    # ── Pump Model / Bowl Size lookups ───────────────────────

    def get_unique_pump_models(self) -> list[str]:
        df = self.get_index_data()
        if "pump_model" in df.columns:
            return sorted(df["pump_model"].dropna().astype(str).unique().tolist())
        return []

    def get_unique_bowl_sizes(self) -> list[float]:
        df = self.get_index_data()
        if "bowl_size" in df.columns:
            return sorted(df["bowl_size"].dropna().unique().tolist())
        return []