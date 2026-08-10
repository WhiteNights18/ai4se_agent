"""PyInstaller recipe for the supported Linux x86_64 single-file executable."""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


datas = collect_data_files(
    "guarded_agent",
    includes=["templates/*.html", "static/*"],
)
datas += copy_metadata("guarded-agent")

hiddenimports = collect_submodules("guarded_agent") + [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.lifespan.on",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
]

a = Analysis(
    ["src/guarded_agent/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="guarded-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
