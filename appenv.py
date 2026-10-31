"""Where the app's files live, and how its helper processes are started.

Two ways to run:
  * from source (./venv/bin/python sort_viewer.py): everything lives in this folder, as before.
  * packaged (the Windows/macOS/Linux app built by PyInstaller, see tcg_catalog.py):
    pages, scripts and the bundled card data are read-only inside the app; the collection,
    backups, logs and caches go to a "TCG Catalog" folder in the user's Documents.

Set TCG_DATA_DIR to put the data folder somewhere else in either mode.
"""
import os, signal, subprocess, sys, time
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))   # pages + bundled data


def _documents():
    if sys.platform == "win32":   # honours a Documents folder moved to OneDrive / another drive
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:   # CSIDL_PERSONAL
                return Path(buf.value)
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            d = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True, env=clean_env()).stdout.strip()
            if d and Path(d).is_dir() and Path(d) != Path.home():
                return Path(d)
        except OSError:
            pass
    d = Path.home() / "Documents"
    return d if d.is_dir() else Path.home()


def clean_env():
    """Environment for programs outside the app (browser, xdg tools): a packaged Linux build
    points LD_LIBRARY_PATH at its own libraries, which breaks other programs."""
    env = dict(os.environ)
    if FROZEN and sys.platform.startswith("linux"):
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


if os.environ.get("TCG_DATA_DIR"):
    DATA_DIR = Path(os.environ["TCG_DATA_DIR"]).expanduser().resolve()
elif FROZEN:
    DATA_DIR = _documents() / "TCG Catalog"
else:
    DATA_DIR = APP_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)


def enter_data_dir():
    """Every path in the app is relative to the data folder."""
    os.chdir(DATA_DIR)


def open_url(url):
    if FROZEN and sys.platform.startswith("linux"):
        try:
            subprocess.Popen(["xdg-open", url], env=clean_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    import webbrowser
    webbrowser.open(url)


def open_folder(path):
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)], env=clean_env(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# Helper processes. Each part of the app is a "role"; from source a role is its own script,
# in the packaged app it is the app itself started with --role <name>.
# ---------------------------------------------------------------------------
ROLES = {"server": "sort_viewer.py", "camera": "phone_cam_server.py", "scanner": "live_scanner.py",
         "finish-batch": "finish_batch.py"}


NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}   # for subprocess.run


def command(role, *args):
    if FROZEN:
        return [sys.executable, "--role", role, *args]
    return [sys.executable, str(APP_DIR / ROLES[role]), *args]


def _same_dir(cwd):
    """Only count processes running on THIS data folder (a second copy of the app must not
    see -- or stop -- another copy's camera server/scanner)."""
    try:
        return cwd is not None and Path(cwd).resolve() == DATA_DIR
    except OSError:
        return False


def _is_role(args, role):
    if len(args) < 2:
        return False
    if FROZEN:
        return any(args[i] == "--role" and args[i + 1] == role for i in range(len(args) - 1)) \
            and os.path.basename(args[0]) == os.path.basename(sys.executable)
    return "python" in os.path.basename(args[0]).lower() and os.path.basename(args[1]) == ROLES[role]


def pids_of(role):
    """PIDs of this data folder's running <role> processes (never matches itself, unlike `pgrep -f`).
    psutil works on Linux, macOS and Windows; falls back to reading /proc on Linux."""
    out = []
    try:
        import psutil
        for p in psutil.process_iter(["pid", "cmdline", "cwd"]):
            try:
                if p.info["pid"] != os.getpid() and _is_role(p.info.get("cmdline") or [], role) and _same_dir(p.info.get("cwd")):
                    out.append(p.info["pid"])
            except Exception:
                continue
        return out
    except ImportError:
        pass
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == os.getpid():
            continue
        try:
            args = [a.decode(errors="ignore") for a in Path(f"/proc/{d}/cmdline").read_bytes().split(b"\0") if a]
            if _is_role(args, role) and _same_dir(os.readlink(f"/proc/{d}/cwd")):
                out.append(int(d))
        except OSError:
            continue
    return out


def spawn(role, args=(), log=None, extra_env=None):
    """Start a role detached (it survives the server restarting), logging to DATA_DIR/<log>."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1", **(extra_env or {})}   # logs show up live on the Home page
    kw = {}
    if os.name == "nt":   # Windows: detach without opening a console window
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    with open(DATA_DIR / (log or f"{role}.log"), "w") as lf:
        subprocess.Popen(command(role, *args), cwd=DATA_DIR, stdin=subprocess.DEVNULL, stdout=lf,
                         stderr=subprocess.STDOUT, env=env, **kw)


def stop(role):
    def kill(pid, hard):
        try:
            import psutil
            pr = psutil.Process(pid)
            pr.kill() if hard else pr.terminate()
        except ImportError:
            os.kill(pid, signal.SIGKILL if hard else signal.SIGTERM)
        except Exception:
            pass
    for pid in pids_of(role):
        kill(pid, False)
    for _ in range(40):
        if not pids_of(role):
            return True
        time.sleep(0.1)
    for pid in pids_of(role):
        kill(pid, True)
    return True
