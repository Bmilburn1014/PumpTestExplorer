# manual_mode/__init__.py
"""
Manual pump-test comparison mode.

Users select pump type, date ranges, model, speed, and test records
from index file data — no PX curve file needed.

Two date-range panels define:
  - Baseline: test data that gets curve-fitted into a reference curve
              (replaces PX baseline in the comparison-results store)
  - Raw:      test data overlaid for comparison (same as auto mode)

The output is written to the same 'comparison-results' dcc.Store,
so existing chart callbacks render it without modification.
"""

from manual_mode.layout import build_manual_panel, manual_id
from manual_mode.callbacks import register_manual_callbacks

__all__ = ["build_manual_panel", "register_manual_callbacks", "manual_id"]
