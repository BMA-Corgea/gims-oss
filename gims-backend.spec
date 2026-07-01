# gims-backend.spec
# Auto-generated + customized for GIMS

import glob
import os  # <-- Import the 'os' module
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

# ─── Hidden imports ──────────────────────────────────────────────────────
hidden_imports = []
hidden_imports += collect_submodules("openpyxl")
hidden_imports += collect_submodules("pandas")
hidden_imports += collect_submodules("numpy")
hidden_imports += collect_submodules("PIL")  # Pillow
hidden_imports += collect_submodules("docxtpl")
hidden_imports += collect_submodules("fastapi")
hidden_imports += collect_submodules("fastapi_users")
hidden_imports += collect_submodules("aiosqlite")
hidden_imports += collect_submodules("httpx")

# ─── Data files from external packages ───────────────────────────────────
datas = []
datas += collect_data_files("openpyxl")
datas += collect_data_files("pandas")
datas += collect_data_files("numpy")
datas += collect_data_files("PIL")
datas += collect_data_files("docxtpl")

# ─── Project assets (recursive globbing for full folders) ─────────────────
# 👇 THIS SECTION HAS BEEN CORRECTED TO PRESERVE FOLDER STRUCTURE 👇
project_folders = [
    "core",
    "gui",
    "api",
    "modules",
    "nodes",
    "projects",
    "static",
    "logins",
]

for folder in project_folders:
    for f in glob.glob(f"{folder}/**/*", recursive=True):
        if os.path.isfile(f):  # Ensure we only add files
            datas.append((f, os.path.dirname(f)))

# ─── Analysis ────────────────────────────────────────────────────────────
a = Analysis(
    ["api/app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gims-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # flip False if you don’t want console window
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="gims-backend",
)
