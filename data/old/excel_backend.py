import pandas as pd
from config import INDEX_FILE_PATH, DETAIL_FILE_DIR

class ExcelBackend:
    def __init__(self):
        self._index_df = pd.read_excel(INDEX_FILE_PATH)

    def get_index_data(self, filters=None):
        df = self._index_df.copy()
        if filters:
            for col, value in filters.items():
                df = df[df[col] == value]
        return df

    def get_detail_data(self, key, filters=None):
        path = DETAIL_FILE_DIR / f"{key}.xlsx"
        df = pd.read_excel(path)
        if filters:
            for col, value in filters.items():
                df = df[df[col] == value]
        return df

    def list_available_keys(self):
        return [f.stem for f in DETAIL_FILE_DIR.glob("*.xlsx")]