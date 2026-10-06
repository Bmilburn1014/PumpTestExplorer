from dash import Input, Output, State, callback, no_update
from data.sync import SyncEngine

@callback(
    Output("sync-status", "children"),
    Output("last-synced", "children"),
    Input("sync-index-btn", "n_clicks"),
    prevent_initial_call=False,
)
def refresh_index(n_clicks):
    sync = SyncEngine()

    if n_clicks and n_clicks > 0:
        sync.sync_index_files()

    status = sync.get_sync_status()
    index_tables = [s for s in status if s["table_name"].startswith("index_")]
    detail_tables = [s for s in status if s["table_name"].startswith("detail_")]

    total_rows = sum(s["row_count"] for s in index_tables)
    msg = (
        f"Index: {len(index_tables)} files, {total_rows:,} rows cached | "
        f"Details: {len(detail_tables)} files cached"
    )

    last = max((s["last_synced"] for s in status), default="Never")
    return msg, f"Last synced: {last}"