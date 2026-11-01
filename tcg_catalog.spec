# PyInstaller build of the whole app as one program (entry point: tcg_catalog.py).
#   ./venv/bin/pip install pyinstaller && ./venv/bin/pyinstaller tcg_catalog.spec
# Output: dist/TCG Catalog/ (Windows, Linux) or dist/TCG Catalog.app (macOS).
# The GitHub workflow (.github/workflows/build.yml) turns that into the installer / .dmg / AppImage.
import os, sys
from pathlib import Path

ROOT = Path(SPECPATH)
NAME = "TCG Catalog"
VERSION = os.environ.get("APP_VERSION", "0.0.0")

pages = [(str(p), ".") for p in sorted(ROOT.glob("*.html")) + sorted(ROOT.glob("*.css")) + sorted(ROOT.glob("*.js"))]
datas = pages + [(str(ROOT / "assets"), "assets"), *[(str(p), "data") for p in sorted((ROOT / "data").glob("catalog_*.json.gz"))]]

a = Analysis(
    ["tcg_catalog.py"],
    pathex=[str(ROOT)],
    datas=datas,
    # parts started as roles or imported lazily
    hiddenimports=["sort_viewer", "phone_cam_server", "live_scanner", "finish_batch", "catalog_sync", "prices",
                   "build_hash_index", "ingest_delver", "qrcode.image.svg"],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=NAME,
    console=False,            # no terminal window; output goes to app.log / the logs on the Home page
    icon=str(ROOT / "assets" / "icon.png"),
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=str(ROOT / "assets" / "icon.png"),
        bundle_identifier="io.github.fernandocc.tcgcatalog",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "LSUIElement": True,   # runs in the background and opens in the browser; quit from the Home page
            "NSLocalNetworkUsageDescription": "TCG Catalog receives the camera picture from your phone over Wi-Fi.",
        },
    )
