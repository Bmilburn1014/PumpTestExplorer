# pump_explorer.spec
# PyInstaller build specification for Pump Test Data Explorer
#
# Build with:  pyinstaller pump_explorer.spec
# Output:      dist/PumpTestExplorer/PumpTestExplorer.exe

import sys
from pathlib import Path

block_cipher = None
base = Path('.').resolve()

print(f"[SPEC] base = {base}")
print(f"[SPEC] assets exists = {(base / 'assets').is_dir()}")
print(f"[SPEC] PSD_Format.xlsx exists = {(base / 'PSD_Format.xlsx').is_file()}")
ico_files = list(base.glob('*.ico'))
print(f"[SPEC] icon files found = {[f.name for f in ico_files]}")

if not (base / 'assets').is_dir():
    raise FileNotFoundError(
        f"assets/ folder not found at {base / 'assets'}. "
        f"Make sure style.css is in the assets/ folder next to server.py."
    )

a = Analysis(
    ['server.py'],
    pathex=[str(base)],
    binaries=[],
    datas=[
        # Bundle the assets folder (CSS) — use absolute path
        (str(base / 'assets'), 'assets'),
        # Bundle the PSD template
        (str(base / 'PSD_Format.xlsx'), '.'),
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
        'plotly.subplots',
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
        # calamine
        'python-calamine',
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
    console=False,          # No console window — app opens in its own GUI
    icon=str(next(base.glob('*.ico'), None)) if list(base.glob('*.ico')) else None,
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
