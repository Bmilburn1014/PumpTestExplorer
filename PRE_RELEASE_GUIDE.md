# Pre-Release Guide: Test Plan, Compilation, and Version Control

This document covers everything needed to take the Pump Test Data Explorer from working development code to a distributable `.exe` with version control.

---

## Table of Contents

1. [Test Plan](#1-test-plan)
2. [Pre-Compilation Checklist](#2-pre-compilation-checklist)
3. [Compiling with PyInstaller](#3-compiling-with-pyinstaller)
4. [TortoiseSVN Setup Guide](#4-tortoisesvn-setup-guide)

---

# 1. Test Plan

## 1.1 Test Matrix Overview

Each test case has a priority (P1 = must pass before release, P2 = should pass, P3 = nice to have) and a pass/fail column for recording results.

---

### 1.2 PX File Loading

| # | Test Case | Steps | Expected Result | Priority | Pass/Fail |
|---|-----------|-------|-----------------|----------|-----------|
| L-1 | Load valid VT PX file | Browse → select a known-good VT .xlsx → Load | Info banner shows model, speed, trim count. Run button enables. | P1 | Pass |
| L-2 | Load valid inline PX file | Browse → select a known-good inline .xlsx → Load | Info banner shows PV/PVF model, speed, trim count. | P1 | Pass |
| L-3 | Load file with no Curve Header Data sheet | Select a non-PX .xlsx | Graceful error message, no crash. | P1 | Pass |
| L-4 | Load file with empty curve sheet | PX file where curve sheet has no data rows | Shows "0 trims" or appropriate warning. | P2 | Pass |
| L-5 | Browse and cancel | Click Browse, then cancel the dialog | Path field unchanged, no error. | P2 | Pass |
| L-6 | Type path manually | Paste a valid path into the text field → Load | Same result as browse. | P2 | Pass |
| L-7 | Invalid path | Type a non-existent path → Load | Error message, no crash. | P1 | Pass |

---

### 1.3 Comparison Engine

| # | Test Case | Steps | Expected Result | Priority | Pass/Fail |
|---|-----------|-------|-----------------|----------|-----------|
| C-1 | VT comparison (standard) | Load VT PX → Run Comparison (no date filter) | Tests found, grouped by trim, charts populated. | P1 | Pass |
| C-2 | Inline comparison | Load inline PX → Run Comparison | Tests found via PPU index, grouped correctly. | P1 | Pass |
| C-3 | Date filter | Set narrow date range → Run | Only tests within range appear. | P1 | Pass |
| C-4 | Date filter — blank | Leave dates blank → Run | All dates included. | P1 | Pass |
| C-5 | Trim tolerance 1% | Set slider to 1% → Run | Fewer groups, tighter grouping. | P2 | Pass |
| C-6 | Trim tolerance 5% | Set slider to 5% → Run | More tests per group, fewer groups. | P2 | Pass |
| C-7 | No matching tests | Load PX for a rare model → Run | "0 matching tests" with diagnostics. No crash. | P1 | Pass |
| C-8 | Progress bar | Run a large comparison | Progress bar fills, status text updates, UI remains responsive. | P2 | Pass |
| C-9 | Run while running | Click Run while a comparison is in progress | "Already running" message, no second thread. | P2 | Pass |
| C-10 | Network file access | Run comparison requiring network detail files | Files copied to local cache, comparison completes. | P1 | Pass |
| C-11 | Progress Bar/Status Bar | Run comparison | Status/Progress bar outputs match log files | P2 | Pass |

---

### 1.4 Affinity Laws

| # | Test Case | Steps | Expected Result | Priority | Pass/Fail |
|---|-----------|-------|-----------------|----------|-----------|
| A-1 | Standard VT (parallel) | Load VT PX with parallel trims → Run | Standard exponents (1,2,3). Check debug log. | P1 | Pass |
| A-2 | Dicmas (conical, Ns > 1500) | Load VT PX where trims are conical (upper ≠ lower) and Ns > 1500 | Dicmas exponents in log. Verify against Fig 2.20 table for the computed Ns. | P1 | Pass |
| A-3 | Conical but Ns ≤ 1500 | Load VT PX with conical trims and low Ns | Falls back to standard (1,2,3). Check log. | P2 | Pass |
| A-4 | BEP from metadata | PX file where row 7 has BEP flow/efficiency | Log shows "BEP from PX metadata". | P2 | Pass |
| A-5 | BEP from curves (fallback) | PX file where row 7 BEP is blank | Log shows "[BEP compute] from curves". Ns still calculated. | P1 | Pass |
| A-6 | Horizontal slip factor | Load inline PX → Run → check log | Log shows "HORIZONTAL slip factor" with eff_d_ratio. | P1 | Pass |
| A-7 | Horizontal dual trim (8.8 x 8.5) | Inline test with "x" format trim | Averaged correctly, slip factor applied to average. | P2 | Pass |
| A-8 | No affinity needed | Tests where trim = baseline exactly | Log shows "[NO AFFINITY]". Data plotted unscaled. | P2 | Pass |

**How to verify affinity math**: Open the debug log (`logs/comparison_debug_*.log`), search for `[AFFINITY]`, and manually check:
- For horizontal: `eff_d = 1.2 × (baseline/test) − 0.2`, then `head_ratio = eff_d²`
- For Dicmas: compute `Ns = RPM × √Q_bep / H_bep^0.75`, look up exponents in Fig 2.20 table, verify `head_ratio = d_ratio^h_exp`

---

### 1.5 Charts & Visualization

| # | Test Case | Steps | Expected Result | Priority | Pass/Fail |
|---|-----------|-------|-----------------|----------|-----------|
| V-1 | Head chart | Click "Head vs Flow" tab | Baseline curves (black), bands (shaded), test markers, fit lines visible. | P1 | Pass |
| V-2 | Power chart | Click "Power vs Flow" tab | Same structure, power values on Y axis. | P1 | Pass |
| V-3 | Efficiency chart | Click "Efficiency vs Flow" tab | Derived efficiency, BEP diamonds, Y axis 0-100%. | P1 | Pass |
| V-4 | Combined chart | Click "Combined" tab | Head on top, power on bottom. Both scroll/zoom together. | P2 | Pass |
| V-5 | PSI units | Toggle to PSI | Head chart Y axis changes to PSI. Power/efficiency unchanged. | P1 | Pass |
| V-6 | Zoom/pan | Scroll to zoom, drag to pan | Chart responds smoothly. | P2 | Pass |
| V-7 | Hover readout | Hover over test point | Tooltip shows test ID, flow, value. Polished tests show "◆ Polished". | P1 | Pass |
| V-8 | Crosshair bar | Move mouse over chart | Crosshair readout updates in real time. | P3 | Pass |
| V-9 | Polished markers (VT) | Load VT data with polished tests (##RA) | Diamond markers with gold border on chart. RA badge in test list. | P1 | Pass |
| V-10 | Failed test markers | Include failing tests | X markers instead of circles. | P2 | Pass |
| V-11 | Outlier fading | Set band to 6%, have tests outside band | Outlier tests appear faded (low opacity). | P2 | Pass |

---

### 1.6 Fit Controls & Shape Tools

| # | Test Case | Steps | Expected Result | Priority | Pass/Fail |
|---|-----------|-------|-----------------|----------|-----------|
| F-1 | Change poly order | Change dropdown from 3 to 5 | Fit line updates to 5th order polynomial. | P1 | Pass |
| F-2 | Apply offset | Set offset to +3% | Fit line shifts up 3%. | P1 | Pass |
| F-3 | Fit range truncation | Set fit range to 75% | Fit line stops at 75% of full range. | P1 | Pass |
| F-4 | Shutoff droop | Enable shutoff, set 5% | Fit line drops near zero flow. | P1 | Pass |
| F-5 | Carryout adjustment | Enable carryout, set 3% | Fit line rises at max flow. | P1 | Pass |
| F-6 | Spline mode | Enable spline | Fit switches from polynomial to smoothed spline. | P1 | Pass |
| F-7 | Smoothing slider | Move smoothing from 0 to 1 | Spline goes from tight-to-data to very smooth. | P2 | Pass |
| F-8 | Knot placement | Enter flow=300, nudge=+2% | Fit line adjusts near 300 GPM. | P2 | Pass |
| F-9 | Show All button | Uncheck some tests, click Show All | All tests re-checked. | P2 | Pass |
| F-10 | Auto Clean | Click Auto Clean with outliers present | Out-of-band tests get unchecked. | P2 | Pass |

---

### 1.7 Trim Groups & Visibility

| # | Test Case | Steps | Expected Result | Priority | Pass/Fail |
|---|-----------|-------|-----------------|----------|-----------|
| G-1 | Hide group | Uncheck a group's checkbox | Group's markers, fit line, and baseline disappear from chart. | P1 | Pass |
| G-2 | Hide individual test | Uncheck one test in a group | That test's markers disappear. Fit line recalculates without it. | P1 | Pass |
| G-3 | Acceptance band slider | Move band from 6% to 3% | Band narrows on chart. More tests flagged as outliers. | P1 | Pass |
| G-4 | Hide baseline | Uncheck "Show PX Baseline Curves" | Black baseline lines and bands disappear. Test data remains. | P2 | Pass |

---

### 1.8 Trim Analysis & Virtual Trims

| # | Test Case | Steps | Expected Result | Priority | Pass/Fail |
|---|-----------|-------|-----------------|----------|-----------|
| T-1 | Open analysis panel | Click "📊 Trim Analysis" | Panel opens showing coverage bars and suggestions. | P1 | Pass |
| T-2 | Add virtual trim | Click "Add Trim" on a suggestion | Dashed baseline appears, tests reassigned, virtual card created. | P1 | Pass |
| T-3 | Virtual trim fit controls | Adjust poly order on virtual group | Virtual fit line updates. | P2 | Pass |

---

### 1.9 Export PSD

| # | Test Case | Steps | Expected Result | Priority | Pass/Fail |
|---|-----------|-------|-----------------|----------|-----------|
| E-1 | Export with visible groups | Adjust fits → Export PSD Curves | Save dialog opens, file created. Open in Excel — verify 30 data points per trim, correct column layout. | P1 | Pass |
| E-2 | Export with hidden groups | Hide one group → Export | Hidden group not in output file. | P1 | Pass |
| E-3 | Export with virtual trims | Add virtual trim → Export | Virtual trim included in output. | P2 | Pass |
| E-4 | Sheet naming (VT) | Export VT file | Sheet named "Model - PartNumber". | P1 | Pass |
| E-5 | Sheet naming (inline) | Export inline file | Sheet named "Model - RPM". | P1 | Pass |
| E-6 | Cancel export | Click Export, then cancel save dialog | "Export cancelled" message. | P2 | Pass |
| E-7 | Missing template | Rename PSD_Format.xlsx → Export | Error message about missing template. | P2 | Pass |
| E-8 | Verify PSD in downstream tool | Import exported PSD into IEQ/PumpSelect | Curves load correctly, BEP and shutoff values present. | P1 | |

---

### 1.10 Session Save & Load

| # | Test Case | Steps | Expected Result | Priority | Pass/Fail |
|---|-----------|-------|-----------------|----------|-----------|
| S-1 | Save session | Adjust fits/visibility → Save Session | JSON file created with all settings. | P1 | Pass |
| S-2 | Load session | Load the saved JSON → Load PX → Run | All controls reflect saved settings (poly order, offsets, checkboxes, sliders, knots). Curves match original. | P1 | Pass |
| S-3 | Load with wrong PX | Load session, but use a different PX file | Settings apply but groups may differ. No crash. | P2 | Pass |
| S-4 | Cancel save/load | Click Save/Load then cancel | "Cancelled" message, no crash. | P2 | Pass |

---

### 1.11 Cache Management

| # | Test Case | Steps | Expected Result | Priority | Pass/Fail |
|---|-----------|-------|-----------------|----------|-----------|
| K-1 | Cache bar updates | Run a comparison (loads detail files) | Cache bar shows increased size. | P1 | Pass |
| K-2 | Clear cache | Click Clear Cache | Bar drops to near zero. Status shows cleared size. | P1 | Pass |
| K-3 | Open logs folder | Click Open Logs Folder | Windows Explorer opens to logs/ directory. | P1 | Pass |
| K-4 | Cache bar colors | Fill cache above 60% / 85% | Bar turns amber / red respectively. | P3 | Pass |

---

### 1.12 Executable-Specific Tests

| # | Test Case | Steps | Expected Result | Priority | Pass/Fail |
|---|-----------|-------|-----------------|----------|-----------|
| X-1 | Launch .exe | Double-click the compiled .exe | Application opens in its own window. | P1 | |
| X-2 | First run (no cache) | Delete cache/ folder → launch .exe | App starts normally, creates cache/ on first comparison. | P1 | |
| X-3 | Network drives accessible | Run comparison requiring network files | Detail files load from network shares. | P1 | |
| X-4 | Browse dialog from .exe | Click Browse in the .exe | PowerShell file dialog opens on top. | P1 | |
| X-5 | Export from .exe | Export PSD from the .exe | Save dialog works, file saves to chosen location. | P1 | |
| X-6 | Paths with spaces | Install .exe in "C:\Program Files\Pump Tool\" | All file operations work correctly. | P1 | |
| X-7 | Run from USB/shared drive | Copy .exe to a network share, run from there | App launches, cache/logs created next to .exe. | P2 | |
| X-8 | Multiple instances | Open two copies of the .exe | Both run independently (separate cache, no port conflicts). | P3 | |
| X-9 | Windows Defender / SmartScreen | Launch .exe on a fresh machine | Note whether SmartScreen blocks it. Document workaround. | P1 | |

---

# 2. Pre-Compilation Checklist

## 2.1 Files to Include

### Required Application Files
```
server.py                    # Entry point
config.py                    # Configuration
layout/
    __init__.py
    main_layout.py
callbacks/
    chart_callbacks.py
    comparison_callbacks.py
    export_callbacks.py
data/
    __init__.py
    affinity.py
    cache_db.py
    column_map.py
    comparison_engine.py
    excel_reader.py
    loader.py
    log_buffer.py
    path_resolver.py
    psd_exporter.py
    pump_classifier.py
    px_curves.py
    sync.py
assets/
    style.css
```

### Required Data Files (bundle alongside .exe)
```
PSD_Format.xlsx              # PSD export template — MUST be next to .exe
```

### Created at Runtime (do NOT bundle)
```
cache/                       # Created on first run
    local_cache.db
logs/                        # Created on first comparison
```

---

## 2.2 requirements.txt

Create this file in the project root:

```
dash>=2.14.0
plotly>=5.18.0
pandas>=2.0.0
numpy>=1.24.0
scipy>=1.11.0
openpyxl>=3.1.0
flaskwebgui>=1.0.0
```

Install with: `pip install -r requirements.txt`

---

## 2.3 Code Hardening Before Compile

### Environment Flag
In `config.py`, set:
```python
ENVIRONMENT = "prod"
```
This switches all file paths from dev (local project directory) to production (network shares). **Verify the network paths in the `else` block of `config.py` are correct for your environment.**

### Verify Network Paths
Open `config.py` and confirm these are correct:
- `INDEX_FILES` → `path` values for each index file
- `DETAIL_FILE_DIR` → base directory for VT detail files
- `PPU_DETAIL_BASE` → base directory for PPU/inline detail files

### Port Conflicts
`server.py` uses port 8050 by default. If multiple instances might run on the same machine, consider:
- Using `port=0` to auto-assign a free port
- Or catching `OSError` on startup and incrementing the port

### Error Handling
Search the codebase for bare `except:` or `except Exception:` blocks that silently swallow errors. In an .exe context, silent failures are harder to debug. Consider adding logging to any bare excepts.

### Temp File Cleanup
The `excel_reader.py` creates a temp directory (`px_detail_*`) in the system temp folder. These persist across sessions. Consider:
- Adding cleanup to the cache clear function (already done)
- Adding an `atexit` handler to clean up on normal shutdown

---

## 2.4 Anti-Virus / SmartScreen Considerations

Unsigned `.exe` files compiled with PyInstaller will trigger Windows SmartScreen ("Windows protected your PC") on first run. Options:

1. **Code signing certificate** (recommended for company distribution): Purchase an EV code signing certificate from DigiCert, Sectigo, or similar. Sign the .exe after compilation. This eliminates SmartScreen warnings immediately.

2. **Company IT whitelist**: Have your IT department whitelist the .exe path or hash in your endpoint protection software.

3. **User instructions**: Document the SmartScreen bypass: "Click 'More info' → 'Run anyway'". Include this in your distribution email.

---

## 2.5 Distribution Package

When distributing the .exe, include:
```
PumpTestExplorer/
    PumpTestExplorer.exe     # The compiled executable
    PSD_Format.xlsx          # Export template
    README.md                # User documentation
```

Users should copy this folder to their local machine (e.g., `C:\PumpTestExplorer\`). Running from a network share works but will be slower on startup.

---

# 3. Compiling with PyInstaller

## 3.1 Install PyInstaller

```
pip install pyinstaller
```

## 3.2 Create the Spec File

Create `pump_explorer.spec` in the project root:

```python
# pump_explorer.spec
import sys
from pathlib import Path

block_cipher = None
base = Path('.').resolve()

a = Analysis(
    ['server.py'],
    pathex=[str(base)],
    binaries=[],
    datas=[
        # Bundle the assets folder (CSS)
        ('assets', 'assets'),
        # Bundle the PSD template
        ('PSD_Format.xlsx', '.'),
    ],
    hiddenimports=[
        # Dash and Plotly internals that PyInstaller misses
        'dash',
        'dash.dash_table',
        'dash.dcc',
        'dash.html',
        'plotly',
        'plotly.graph_objects',
        'plotly.express',
        'flask',
        'flask_compress',
        # Scipy submodules used by the app
        'scipy.interpolate',
        'scipy.interpolate._bspl',
        'scipy.interpolate._fitpack_impl',
        'scipy.special',
        'scipy.special._ufuncs',
        # Openpyxl
        'openpyxl',
        'openpyxl.cell',
        'openpyxl.styles',
        # FlaskWebGui
        'flaskwebgui',
        # App modules
        'callbacks.chart_callbacks',
        'callbacks.comparison_callbacks',
        'callbacks.export_callbacks',
        'data.affinity',
        'data.cache_db',
        'data.column_map',
        'data.comparison_engine',
        'data.excel_reader',
        'data.loader',
        'data.log_buffer',
        'data.path_resolver',
        'data.psd_exporter',
        'data.pump_classifier',
        'data.px_curves',
        'data.sync',
        'layout.main_layout',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter',      # Not needed on Windows (uses PowerShell dialogs)
        'matplotlib',   # Not used
        'IPython',      # Not used
        'notebook',     # Not used
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PumpTestExplorer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # No console window
    icon=None,              # Add 'icon.ico' path here if you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='PumpTestExplorer',
)
```

## 3.3 Build the Executable

```
pyinstaller pump_explorer.spec
```

The output goes to `dist/PumpTestExplorer/`. The main .exe is `dist/PumpTestExplorer/PumpTestExplorer.exe`.

## 3.4 Common PyInstaller Issues

| Problem | Symptom | Fix |
|---------|---------|-----|
| Missing module | `ModuleNotFoundError` at runtime | Add to `hiddenimports` in the spec file |
| Missing data file | Template not found, CSS missing | Add to `datas` in the spec file |
| Dash assets not loading | App loads but has no styling | Verify `('assets', 'assets')` is in `datas` |
| scipy errors | `ImportError: DLL load failed` | Add specific scipy submodules to `hiddenimports` |
| antivirus blocks | .exe quarantined during build | Add build directory to AV exclusions |
| Large file size | .exe is 200+ MB | Use `excludes` to remove unused packages. Consider UPX compression. |

## 3.5 Testing the Build

After compiling:

1. Copy the entire `dist/PumpTestExplorer/` folder to a clean machine (or a folder outside your dev environment)
2. Ensure `PSD_Format.xlsx` is in the same folder as the .exe
3. Double-click `PumpTestExplorer.exe`
4. Run through the X-series tests from the test plan (section 1.12)
5. Verify network paths resolve correctly
6. Check that `cache/` and `logs/` folders are created next to the .exe

---

# 4. TortoiseSVN Setup Guide

## 4.1 Concepts

SVN (Subversion) is a version control system. TortoiseSVN is a Windows shell extension that integrates SVN into Windows Explorer — you interact with it through right-click context menus, not command lines.

Key terms:
- **Repository** (repo): The central server where all versions of your code are stored
- **Working copy**: Your local folder that's linked to the repository
- **Commit**: Upload your changes to the repository (creates a new version)
- **Update**: Download other people's changes from the repository
- **Revision**: A numbered snapshot of the entire project (r1, r2, r3, ...)
- **Trunk**: The main development line (like "main" in Git)
- **Tags**: Named snapshots for releases (like bookmarks)
- **Branches**: Parallel development lines (rarely needed for small teams)

## 4.2 Install TortoiseSVN

1. Download from https://tortoisesvn.net/downloads.html
2. Run the installer — accept defaults
3. **Restart your computer** (required for shell integration)
4. After restart, right-click any folder — you should see TortoiseSVN in the context menu

## 4.3 Create the Repository

You need a place to host the SVN repository. Options:

**Option A: Network share (simplest for a small team)**
1. Create a folder on a shared drive, e.g., `\\server\svn\PumpTestExplorer`
2. Right-click that folder → TortoiseSVN → **Create repository here**
3. TortoiseSVN creates the repository structure automatically
4. Your repository URL will be: `file:///\\server/svn/PumpTestExplorer`

**Option B: SVN server (better for larger teams)**
- Use VisualSVN Server (free, Windows) — install on a server machine
- Provides `https://` URLs and user authentication
- More robust but requires IT setup

## 4.4 Create the Standard Folder Structure

Before importing your code, create the standard SVN layout:

1. Open your repository in the **TortoiseSVN Repo Browser**: right-click any folder → TortoiseSVN → **Repo-browser** → enter your repository URL
2. Right-click in the repo browser → **Create folder** → name it `trunk`
3. Create another folder called `tags`
4. Create another folder called `branches`

Your repository should look like:
```
PumpTestExplorer/
    trunk/          ← Your active code goes here
    tags/           ← Release snapshots go here
    branches/       ← Future parallel work (if needed)
```

## 4.5 Import Your Code (First Time)

1. Navigate to your project folder in Windows Explorer (the one containing `server.py`)
2. Right-click the project folder → TortoiseSVN → **Import...**
3. In the URL field, enter: `<your-repo-url>/trunk`
   - Example: `file:///\\server/svn/PumpTestExplorer/trunk`
4. In the message field, type: `Initial import of Pump Test Data Explorer v1.0`
5. Click **OK**

**Important**: This uploads your code but does NOT make your current folder a working copy. You need to check out (next step).

## 4.6 Check Out a Working Copy

1. Go to the parent folder where you want your working copy (e.g., `C:\Projects\`)
2. Right-click → SVN **Checkout...**
3. URL: `<your-repo-url>/trunk`
4. Checkout directory: `C:\Projects\PumpTestExplorer`
5. Click **OK**

You now have a working copy. Every file will show a green checkmark overlay icon in Windows Explorer.

## 4.7 What to Exclude from Version Control

Before your first commit from the working copy, tell SVN to ignore generated files:

1. In your working copy folder, right-click the following folders/files → TortoiseSVN → **Add to ignore list**:

```
cache/                  # SQLite cache (generated at runtime)
logs/                   # Debug logs (generated at runtime)
__pycache__/            # Python bytecode
*.pyc                   # Python bytecode
dist/                   # PyInstaller output
build/                  # PyInstaller build temp
*.spec                  # Can include if you want, but optional
callbacks/__pycache__/
data/__pycache__/
layout/__pycache__/
callbacks/old/          # Old/deprecated code
data/old/               # Old/deprecated code
layout/old/             # Old/deprecated code
```

2. After adding ignore rules, **commit** the ignore settings: right-click the project folder → SVN **Commit** → message: `Add SVN ignore rules`

## 4.8 Daily Workflow

### Making Changes

1. Edit files normally in your editor
2. Modified files get a **red exclamation mark** overlay in Explorer
3. New files get a **blue question mark** — you need to add them

### Committing Changes

1. Right-click the project folder → SVN **Commit...**
2. TortoiseSVN shows all changed files with checkboxes
3. Review the changes — double-click any file to see a diff
4. Uncheck any files you don't want to commit yet
5. **Add** any new files (shown with "non-versioned" status) by checking them
6. Write a descriptive commit message, for example:
   - `Fix Dicmas exponents — use Fig 2.20 regression, compute BEP from curves when metadata missing`
   - `Add polished impeller detection and diamond markers`
   - `Update trim tolerance slider max to 5%`
7. Click **OK**

### Getting Updates (Multi-Person Team)

1. Right-click project folder → SVN **Update**
2. TortoiseSVN downloads any changes others have committed
3. If there's a conflict (you both edited the same file), TortoiseSVN shows a merge dialog

### Good Commit Message Examples

```
Fix: Cache not clearing — add VACUUM after DROP TABLE
Add: PSD export with session save/load and cache management
Add: Dicmas Fig 2.20 affinity exponents for conical VT impellers
Fix: Session load now hydrates all UI controls (poly, offset, knots, etc)
Fix: Model matching — expand _KNOWN_BASE_MODELS, add prefix fallback
Change: Trim tolerance slider max 10% → 5%
```

## 4.9 Tagging a Release

When you're ready to distribute a version:

1. Right-click project folder → TortoiseSVN → **Branch/tag...**
2. To URL: `<your-repo-url>/tags/v1.0` (or whatever version)
3. Message: `Tag release v1.0`
4. Click **OK**

This creates a frozen snapshot. You can always go back to it.

## 4.10 Viewing History

- Right-click any file → TortoiseSVN → **Show log** — see all commits that touched that file
- Right-click any file → TortoiseSVN → **Diff with previous version** — see what changed
- Right-click project folder → TortoiseSVN → **Revision graph** — visual timeline of all versions

## 4.11 SVN Tips for Engineers

- **Commit often, commit small**: One logical change per commit. "Fix affinity exponents" not "Fix everything from this week"
- **Always update before committing**: Avoids merge conflicts
- **Write meaningful messages**: Future-you will thank present-you
- **Don't commit generated files**: No `.pyc`, no `cache/`, no `dist/`. These bloat the repo and cause merge conflicts
- **Don't commit secrets**: No passwords, API keys, or connection strings. Use `config.py` environment variables instead
- **Commit the spec file**: `pump_explorer.spec` is hand-edited and should be version controlled
- **Tag every release**: Before distributing a new .exe, tag it. This makes it easy to recreate any version

---

*This document was created for the Pump Test Data Explorer pre-release preparation.*
