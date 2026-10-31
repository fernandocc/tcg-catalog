#!/usr/bin/env python3
"""Open TCG Catalog: start the app if it isn't running, otherwise just open it in the browser.
Used by every launcher (launch_app.sh and the desktop shortcut, when run from source)."""
import os, subprocess, sys, time, urllib.request, webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
PORT_FILE = ROOT / ".sort_viewer_port"


def running_port():
    try:
        port = PORT_FILE.read_text().strip()
        urllib.request.urlopen(f"http://localhost:{port}/api/status", timeout=2).read(64)
        return port
    except Exception:
        return None


port = running_port()
if port:
    webbrowser.open(f"http://localhost:{port}/")
    sys.exit(0)

PORT_FILE.unlink(missing_ok=True)
kw = {}
if os.name == "nt":
    kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
else:
    kw["start_new_session"] = True
with open(ROOT / "sort_viewer.log", "a") as log:
    subprocess.Popen([sys.executable, "sort_viewer.py"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, **kw)
# sort_viewer.py opens the browser itself once it is serving; wait briefly so a double-click
# right after doesn't start a second copy
for _ in range(30):
    if running_port():
        break
    time.sleep(0.3)
