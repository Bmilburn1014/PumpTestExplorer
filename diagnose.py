#!/usr/bin/env python3
"""
diagnose.py — Run this to trace exactly where the comparison pipeline
fails to find matching tests.

Usage:  python diagnose.py
"""

import sys
import os
import pandas as pd

# Ensure we can import from project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import INDEX_FILES, BASE_DIR, ENVIRONMENT
from data.px_curves import read_px_curves
from data.pump_classifier import classify_pump, build_model_search_pattern


SEP = "=" * 60

print(SEP)
print("PUMP TEST EXPLORER — DIAGNOSTIC")
print(SEP)
print(f"Environment: {ENVIRONMENT}")
print(f"Base dir:    {BASE_DIR}")
print()

# ── Step 1: Check index files exist ──────────────────────────
print(SEP)
print("STEP 1: INDEX FILES")
print(SEP)
for cfg in INDEX_FILES:
    path = cfg["path"]
    exists = os.path.exists(path)
    print(f"  {cfg['name']}")
    print(f"    Path:   {path}")
    print(f"    Exists: {'YES' if exists else '*** NO ***'}")
    if exists:
        size = os.path.getsize(path) / 1024
        print(f"    Size:   {size:.0f} KB")
    print()

# ── Step 2: Read index files raw and show columns ────────────
print(SEP)
print("STEP 2: RAW COLUMN HEADERS IN EACH INDEX FILE")
print(SEP)
for cfg in INDEX_FILES:
    path = cfg["path"]
    if not os.path.exists(path):
        print(f"  {cfg['name']}: SKIPPED (file not found)")
        continue

    try:
        df = pd.read_excel(path, sheet_name=cfg.get("sheet", 0),
                           engine="openpyxl", nrows=5)
        print(f"\n  {cfg['name']} — {len(df.columns)} columns:")
        for i, col in enumerate(df.columns):
            print(f"    [{i:2d}] {repr(col)}")
    except Exception as e:
        print(f"  {cfg['name']}: ERROR reading — {e}")
    print()

# ── Step 3: Check column mapping matches ─────────────────────
print(SEP)
print("STEP 3: COLUMN MAPPING MATCH CHECK")
print(SEP)
for cfg in INDEX_FILES:
    path = cfg["path"]
    if not os.path.exists(path):
        continue

    try:
        df = pd.read_excel(path, sheet_name=cfg.get("sheet", 0),
                           engine="openpyxl", nrows=5)
    except Exception:
        continue

    actual_cols = list(df.columns)
    actual_stripped = {str(c).strip(): c for c in actual_cols}
    mapping = cfg.get("columns", {})

    print(f"\n  {cfg['name']}:")
    matched = 0
    missed = 0
    for std_name, excel_header in sorted(mapping.items()):
        if excel_header in actual_cols:
            print(f"    ✓ {std_name:20s} → {repr(excel_header)}")
            matched += 1
        elif excel_header.strip() in actual_stripped:
            real = actual_stripped[excel_header.strip()]
            print(f"    ~ {std_name:20s} → {repr(excel_header)} "
                  f"(WHITESPACE MISMATCH — actual: {repr(real)})")
            matched += 1
        else:
            # Try case-insensitive
            found = False
            for ac in actual_cols:
                if str(ac).strip().lower() == excel_header.strip().lower():
                    print(f"    ~ {std_name:20s} → {repr(excel_header)} "
                          f"(CASE MISMATCH — actual: {repr(ac)})")
                    found = True
                    break
            if not found:
                print(f"    ✗ {std_name:20s} → {repr(excel_header)} "
                      f"*** NOT FOUND ***")
                missed += 1

    print(f"\n    Summary: {matched} matched, {missed} missing")
    print()

# ── Step 4: Try PX file ─────────────────────────────────────
print(SEP)
print("STEP 4: PX FILE")
print(SEP)

# Look for any .xlsx in base dir that might be a PX file
px_candidates = [f for f in os.listdir(BASE_DIR)
                 if f.lower().endswith('.xlsx') and 'PV' in f.upper()]

if px_candidates:
    print(f"  Possible PX files in {BASE_DIR}:")
    for f in px_candidates:
        print(f"    {f}")
    px_path = os.path.join(str(BASE_DIR), px_candidates[0])
else:
    # Try the uploaded example
    px_path = None
    for candidate in [
        os.path.join(str(BASE_DIR), "Example_Base_Curves_file.xlsx"),
    ]:
        if os.path.exists(candidate):
            px_path = candidate
            break

if px_path and os.path.exists(px_path):
    print(f"\n  Testing with: {px_path}")
    px = read_px_curves(px_path)
    print(f"  Curve number: {px.curve_number}")
    print(f"  Speed: {px.rated_speed} RPM")
    print(f"  Trims: {[t.diameter for t in px.trims]}")

    cls = classify_pump(px.curve_number)
    print(f"\n  Classification:")
    print(f"    Type:       {cls.pump_type}")
    print(f"    Index:      {cls.index_name}")
    print(f"    Trim col:   {cls.trim_column}")

    pattern = build_model_search_pattern(px.curve_number)
    print(f"    Search regex: {pattern}")

    # ── Step 5: Try the actual search ────────────────────────
    print(f"\n{SEP}")
    print("STEP 5: INDEX SEARCH SIMULATION")
    print(SEP)

    target_index = cls.index_name
    target_cfg = None
    for cfg in INDEX_FILES:
        if cfg["name"] == target_index:
            target_cfg = cfg
            break

    if target_cfg and os.path.exists(target_cfg["path"]):
        df = pd.read_excel(target_cfg["path"],
                           sheet_name=target_cfg.get("sheet", 0),
                           engine="openpyxl")
        print(f"  Index loaded: {len(df)} rows, {len(df.columns)} cols")

        # Normalize columns (with strip fix)
        col_mapping = target_cfg.get("columns", {})
        actual_cols = {str(c).strip(): c for c in df.columns}
        rename_map = {}
        for std_name, excel_header in col_mapping.items():
            # Try exact
            if excel_header in df.columns:
                rename_map[excel_header] = std_name
            # Try stripped
            elif excel_header.strip() in actual_cols:
                rename_map[actual_cols[excel_header.strip()]] = std_name
            # Try case-insensitive
            else:
                for real_col in df.columns:
                    if str(real_col).strip().lower() == excel_header.strip().lower():
                        rename_map[real_col] = std_name
                        break

        df = df.rename(columns=rename_map)
        print(f"  After normalization: columns include "
              f"{[c for c in df.columns if not c.startswith('Unnamed')][:15]}")

        if "pump_model" in df.columns:
            models = df["pump_model"].dropna().astype(str).unique()
            print(f"\n  Unique pump_model values ({len(models)} total):")
            for m in sorted(models)[:30]:
                print(f"    {repr(m)}")
            if len(models) > 30:
                print(f"    ... and {len(models) - 30} more")

            # Test regex match
            import re
            matches = [m for m in models
                       if re.search(pattern, m, re.IGNORECASE)]
            print(f"\n  Regex '{pattern}' matches {len(matches)} models:")
            for m in matches[:20]:
                print(f"    ✓ {m}")

            if not matches:
                print("\n  *** NO MATCHES! ***")
                print("  Trying broader search (just 'PV'):")
                broad = [m for m in models if 'PV' in m.upper()]
                for m in broad[:20]:
                    print(f"    {m}")
                if not broad:
                    print("    No models contain 'PV' at all.")
        else:
            print("\n  *** 'pump_model' column NOT FOUND after normalization ***")
            print("  Available columns:")
            for c in df.columns:
                print(f"    {repr(c)}")

        # Check trim column
        trim_col = cls.trim_column
        if trim_col in df.columns:
            trims = df[trim_col].dropna().unique()
            print(f"\n  Trim column '{trim_col}' — {len(trims)} unique values:")
            for t in sorted(trims, key=str)[:20]:
                print(f"    {repr(t)}")
        else:
            print(f"\n  *** Trim column '{trim_col}' NOT FOUND ***")

        # Check date column
        if "test_date" in df.columns:
            dates = pd.to_datetime(df["test_date"], errors="coerce")
            valid = dates.dropna()
            if len(valid) > 0:
                print(f"\n  Date range: {valid.min()} to {valid.max()}")
            else:
                print(f"\n  test_date column exists but no parseable dates")
                print(f"  Sample values: {df['test_date'].dropna().head().tolist()}")
        else:
            print(f"\n  *** 'test_date' column NOT FOUND ***")

    else:
        print(f"  *** Index file for '{target_index}' not found ***")

else:
    print("  No PX file found to test with.")


print(f"\n{SEP}")
print("DIAGNOSTIC COMPLETE")
print(SEP)