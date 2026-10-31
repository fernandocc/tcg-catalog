#!/usr/bin/env python3
"""Single entry point of the packaged app (PyInstaller builds this file, see tcg_catalog.spec).

    TCG Catalog                     open the app: start it if it isn't running, else just open the browser
    TCG Catalog --role camera       the phone camera server   (started by the app itself)
    TCG Catalog --role scanner URL  the card scanner          (started by the app itself)
    TCG Catalog --role finish-batch merge the current batch   (started by the app itself)

From source, the same parts run as their own scripts (sort_viewer.py, live_scanner.py, ...).
"""
import os, sys, urllib.request


def _running_port():
    import appenv
    try:
        port = (appenv.DATA_DIR / ".sort_viewer_port").read_text().strip()
        urllib.request.urlopen(f"http://localhost:{port}/api/status", timeout=2).read(64)
        return port
    except Exception:
        return None


def _linux_menu_entry():
    """AppImage: add TCG Catalog to the applications menu the first time it runs (and after it moves)."""
    image = os.environ.get("APPIMAGE")
    if not image:
        return
    import appenv
    from pathlib import Path
    apps = Path.home() / ".local" / "share" / "applications"
    icon = appenv.DATA_DIR / ".icon.svg"
    entry = ("[Desktop Entry]\nType=Application\nName=TCG Catalog\nComment=Scan, sort and browse your trading cards\n"
             f'Exec="{image}"\nIcon={icon}\nTerminal=false\nCategories=Utility;\n')
    f = apps / "tcg-catalog.desktop"
    try:
        if not f.exists() or f.read_text() != entry:
            apps.mkdir(parents=True, exist_ok=True)
            icon.write_bytes((appenv.APP_DIR / "assets" / "icon.svg").read_bytes())
            f.write_text(entry)
    except OSError:
        pass


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]   # macOS Finder adds -psn_<id>
    role, rest = "app", args
    if len(args) >= 2 and args[0] == "--role":
        role, rest = args[1], args[2:]
    import appenv
    appenv.enter_data_dir()
    if sys.stdout is None or sys.stderr is None:   # windowed build: no console to print to
        log = open(appenv.DATA_DIR / "app.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    sys.argv = [appenv.ROLES.get(role, "tcg_catalog.py"), *rest]

    if role == "camera":
        import phone_cam_server
        phone_cam_server.main()
    elif role == "scanner":
        import live_scanner
        live_scanner.main()
    elif role == "finish-batch":
        import finish_batch   # runs on import
    elif role in ("app", "server"):
        if role == "app":
            port = _running_port()
            if port:
                appenv.open_url(f"http://localhost:{port}/")
                return
            _linux_menu_entry()
        import sort_viewer
        sort_viewer.main()
    else:
        raise SystemExit(f"unknown role {role!r}")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
