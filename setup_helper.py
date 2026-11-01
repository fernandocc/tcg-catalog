#!/usr/bin/env python3
"""Second half of the installer (install.sh runs this with the app's own Python).

1. Creates the collection database (if it doesn't exist yet).
2. (The bundled card catalogs + recognition fingerprints, data/catalog_<game>.json.gz, are
   loaded by the app itself on its first start -- no gigabytes of image downloads.)
3. Puts a "TCG Catalog" shortcut on the desktop (and in the app menu on Linux).

Safe to run again: existing cards and your collection are never overwritten.
Set TCG_NO_SHORTCUTS=1 to skip step 3.
"""
import os, platform, sqlite3, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
import collection_core as core

DB = ROOT / "collection.db"
NAME = "TCG Catalog"


def say(msg):
    print(f"  {msg}", flush=True)


def install_card_data():
    db = sqlite3.connect(DB)
    core.schema(db)
    db.commit()
    have = db.execute("SELECT COUNT(*) FROM card_hashes").fetchone()[0]
    say(f"Card data already installed ({have:,} recognisable cards)." if have else
        "Card data will be installed when the app opens (takes a few seconds).")


def desktop_dir():
    if platform.system() == "Linux":
        try:
            d = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True).stdout.strip()
            if d:
                return Path(d)
        except OSError:
            pass
    return Path.home() / "Desktop"


def make_shortcuts():
    system = platform.system()
    desk = desktop_dir()
    if system == "Linux":
        entry = (f"[Desktop Entry]\nType=Application\nName={NAME}\nComment=Scan, sort and browse your trading cards\n"
                 f"Exec={ROOT / 'launch_app.sh'}\nPath={ROOT}\nIcon={ROOT / 'assets' / 'icon.svg'}\nTerminal=false\nCategories=Utility;\n")
        apps = Path.home() / ".local" / "share" / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        (apps / "tcg-catalog.desktop").write_text(entry)
        targets = [apps / "tcg-catalog.desktop"]
        if desk.is_dir():
            d = desk / f"{NAME}.desktop"
            d.write_text(entry)
            d.chmod(0o755)
            subprocess.run(["gio", "set", str(d), "metadata::trusted", "true"], capture_output=True)
            targets.append(d)
        say("Shortcut created: " + ", ".join(str(t) for t in targets))
    elif system == "Darwin":
        cmd = desk / f"{NAME}.command"
        cmd.write_text(f'#!/bin/bash\ncd "{ROOT}"\n./venv/bin/python launch.py\nosascript -e \'tell application "Terminal" to close front window\' >/dev/null 2>&1 &\n')
        cmd.chmod(0o755)
        say(f"Shortcut created: {cmd}  (double-click it to open the app)")
    elif system == "Windows":
        say("On Windows, use the TCG Catalog installer from the Releases page instead (it adds the shortcuts).")
    else:
        say(f"No shortcut for {system}; start the app with launch.py.")


if __name__ == "__main__":
    print("Setting up TCG Catalog")
    install_card_data()
    if os.environ.get("TCG_NO_SHORTCUTS") != "1":
        make_shortcuts()
    print("Done.")
