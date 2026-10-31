#!/usr/bin/env python3
"""
One-command workflow: pick a Delver CSV in the browser, it gets ingested
server-side, then the sorting assistant opens automatically -- no separate
ingest_delver.py invocation needed.

Sorting assistant: walk the scanned stack in original scan order, one card at a
time, with its image/name/set shown big. Press SPACE to advance, Backspace/Left
to go back. Cards the API couldn't resolve are flagged NEEDS REVIEW in red.

Live camera scanning (live_scanner.py) and future TCG importers write into the
same shared collection_core schema -- this page just re-reads collection.db,
no per-source wiring needed here.

Usage:
  ./venv/bin/python sort_viewer.py            # opens the upload page in your browser

(ingest_delver.py standalone still works too, e.g. for batch/scripted ingests.)
"""
import json, socket, sqlite3
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import collection_core as core
import ingest_delver as ing

DB = "collection.db"
UPLOAD_DIR = Path("uploads")

# Fixed, not random -- convenient to bookmark/remember across restarts.
# Falls back to a random port only if this one's taken (rare for a personal tool).
PREFERRED_PORT = 8765

def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", PREFERRED_PORT))
            return PREFERRED_PORT
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]

OWNED_SQL = """
    SELECT card_id, SUM(cur) AS cur, SUM(prev) AS prev FROM (
        SELECT card_id, qty AS cur, 0 AS prev FROM collection
        UNION ALL
        SELECT card_id, 0, qty FROM prior_collection)
    GROUP BY card_id"""


def _owned_db():
    db = sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS prior_collection (
        card_id TEXT, variant TEXT, condition TEXT, language TEXT,
        qty INTEGER NOT NULL DEFAULT 0, batch TEXT,
        PRIMARY KEY (card_id, variant, condition, language, batch))""")
    return db


def owned_sets():
    """Every set with at least one owned card, across the current scan AND all
    earlier finished batches (queried fresh from the db on each call)."""
    db = _owned_db()
    rows = db.execute(f"""
        SELECT s.id, s.name, COUNT(*), SUM(o.cur), SUM(o.prev)
        FROM ({OWNED_SQL}) o JOIN cards c ON c.id = o.card_id JOIN sets s ON s.id = c.set_id
        GROUP BY s.id ORDER BY s.name""").fetchall()
    db.close()
    return [{"id": i, "name": n, "distinct": d, "current": cur, "earlier": prev} for i, n, d, cur, prev in rows]


FINISH_OF = {"normal": "standard", "holo": "foil", "reverse": "parallel", "pokeball": "parallel", "masterball": "parallel"}
FOIL_ONLY = ("double rare", "ultra rare", "rare ultra", "illustration rare", "special illustration rare", "hyper rare",
             "mega hyper rare", "rare secret", "rare rainbow", "shiny rare", "rare shiny", "ace spec")


def possible_finishes(rarity, release):
    """Which of the player's-guide boxes (standard / foil / parallel) a card can have,
    inferred from catalog rarity -- the catalog stores no per-finish data. Returns
    (finishes, known); known=False means rarity was blank/unrecognised so all three are shown."""
    r = (rarity or "").strip().lower()
    if r in ("c", "common", "u", "uncommon"):
        return ["standard", "parallel"], True
    if any(r == x or r.startswith(x) for x in FOIL_ONLY):
        return ["foil"], True
    if r in ("r", "rare"):
        # Scarlet & Violet onward: plain Rares are holofoil (plus a parallel); older Rares also came non-holo
        return (["foil", "parallel"] if (release or "") >= "2023/03/31" else ["standard", "foil", "parallel"]), True
    if r.startswith("rare holo") or r.startswith("trainer gallery"):
        return ["foil", "parallel"], True
    return ["standard", "foil", "parallel"], False


def owned_finishes(db):
    """{card_id: {"standard": qty, "foil": qty, "parallel": qty}} across the current scan and all earlier batches."""
    out = {}
    for card_id, variant, qty in db.execute("""
            SELECT card_id, variant, SUM(qty) FROM (
                SELECT card_id, variant, qty FROM collection
                UNION ALL SELECT card_id, variant, qty FROM prior_collection) GROUP BY card_id, variant"""):
        f = FINISH_OF.get(variant, "standard")
        out.setdefault(card_id, {})
        out[card_id][f] = out[card_id].get(f, 0) + qty
    return out


def _card_dict(row, fin):
    i, n, num, r, img, cur, prev, sn, rel = row
    possible, known = possible_finishes(r, rel)
    owned = fin.get(i, {})
    # a finish we own but that rarity says is impossible (rarity data is imperfect): still show it
    for f in owned:
        if f not in possible:
            possible = possible + [f]
    return {"id": i, "name": n, "number": num, "rarity": r, "image": img, "current": cur, "earlier": prev,
            "set": sn, "possible": possible, "finish_known": known, "finishes": owned}


def card_finishes(card_id):
    db = _owned_db()
    row = db.execute("SELECT c.rarity, s.release_date FROM cards c LEFT JOIN sets s ON s.id = c.set_id WHERE c.id = ?", (card_id,)).fetchone()
    fin = owned_finishes(db)
    db.close()
    if not row:
        return {"possible": [], "finishes": {}, "finish_known": False}
    possible, known = possible_finishes(row[0], row[1])
    owned = fin.get(card_id, {})
    return {"possible": possible + [f for f in owned if f not in possible], "finishes": owned, "finish_known": known, "rarity": row[0]}


def search_cards(q, include_missing=False, limit=400):
    """Search owned cards (or the whole catalog with include_missing) by card name,
    card number (leading zeros ignored) or set name, across every set."""
    q = q.strip()
    if len(q) < 2:
        return {"cards": [], "truncated": False}
    db = _owned_db()
    import unicodedata
    # fold(): lowercase + strip accents, so typing "pokemon" finds "Pokémon" (and é matches e)
    fold = lambda t: "".join(ch for ch in unicodedata.normalize("NFD", t or "") if unicodedata.category(ch) != "Mn").lower()
    db.create_function("fold", 1, fold)
    join = "LEFT JOIN" if include_missing else "JOIN"
    like = f"%{fold(q)}%"
    rows = db.execute(f"""
        SELECT c.id, c.name, c.number, c.rarity, c.image_url, COALESCE(o.cur, 0), COALESCE(o.prev, 0), s.name, s.release_date
        FROM cards c {join} ({OWNED_SQL}) o ON o.card_id = c.id LEFT JOIN sets s ON s.id = c.set_id
        WHERE fold(c.name) LIKE ? OR fold(s.name) LIKE ? OR LTRIM(c.number, '0') = LTRIM(?, '0')
        ORDER BY s.release_date DESC, s.name, c.id LIMIT ?""", (like, like, q, limit + 1)).fetchall()
    fin = owned_finishes(db)
    db.close()
    return {"cards": [_card_dict(r, fin) for r in rows[:limit]], "truncated": len(rows) > limit}


def set_cards(set_id, include_missing=False):
    """Cards of one set (current scan + earlier batches) with per-source qty. With
    include_missing, every card in the catalog for that set comes back, unowned ones
    with qty 0. Also returns the set's official card count for the "owned / total" line."""
    db = _owned_db()
    join = "LEFT JOIN" if include_missing else "JOIN"
    rows = db.execute(f"""
        SELECT c.id, c.name, c.number, c.rarity, c.image_url, COALESCE(o.cur, 0), COALESCE(o.prev, 0), s.name, s.release_date
        FROM cards c {join} ({OWNED_SQL}) o ON o.card_id = c.id LEFT JOIN sets s ON s.id = c.set_id WHERE c.set_id = ?""", (set_id,)).fetchall()
    total = db.execute("SELECT printed_total, total FROM sets WHERE id = ?", (set_id,)).fetchone()
    fin = owned_finishes(db)
    db.close()
    return {"cards": [_card_dict(r, fin) for r in rows],
            "printed_total": total[0] if total else None, "total": total[1] if total else None}


# ---------------------------------------------------------------------------
# Control panel API: start/stop the phone camera server and the scanner, merge a batch,
# back up the db -- so nothing needs a terminal. These are only honoured from this machine
# (this server listens on the LAN; process control must not be reachable from other devices).
# ---------------------------------------------------------------------------
import os, signal, subprocess, sys, time, shutil, urllib.request

import appenv
PROJECT = appenv.DATA_DIR   # collection, logs, runtime files (the app folder when run from source)
QT_ENV = {}  # scanner runs headless now (web pages show the live view); set SCANNER_WINDOW=1 to get the Qt window
CAM_URL = "http://127.0.0.1:8081"
pids_of, _stop = appenv.pids_of, appenv.stop


def _spawn(role, args, log, extra_env=None):
    appenv.spawn(role, args, log, extra_env)


def _torch_off():
    try:
        urllib.request.urlopen(urllib.request.Request(f"{CAM_URL}/torch?on=0", method="POST"), timeout=0.5).read()
    except Exception:
        pass


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def control_status():
    cam = bool(pids_of("camera"))
    age = None
    if cam:
        try:
            # read a bounded amount: an older camera server answers unknown paths with an endless MJPEG stream
            age = json.loads(urllib.request.urlopen(f"{CAM_URL}/health", timeout=0.6).read(512)).get("age")
        except Exception:
            pass
    db = _owned_db()
    live = db.execute("SELECT COUNT(*) FROM scan_log").fetchone()[0]
    merged = db.execute("SELECT COUNT(DISTINCT batch), COALESCE(SUM(qty), 0) FROM prior_collection").fetchone()
    db.close()
    return {"camera": cam, "scanner": bool(pids_of("scanner")),
            "phone_streaming": age is not None and age < 4, "phone_age": age,
            "phone_url": f"https://{lan_ip()}:8443", "live_scans": live,
            "batches": merged[0], "copies": merged[1], "setup": setup_counts(), "data_dir": str(PROJECT)}


def control_action(path, req):
    act = req.get("action")
    if path == "/api/camera":
        if act == "start":
            if not pids_of("camera"):
                _spawn("camera", [], "phone_cam.log")
        elif act == "stop":
            _stop("camera")
        else:
            return {"ok": False, "error": "action must be start or stop"}
    elif path == "/api/scanner":
        if act == "start":
            if not pids_of("camera"):
                _spawn("camera", [], "phone_cam.log")
                time.sleep(1.5)
            if not pids_of("scanner"):
                _spawn("scanner", [CAM_URL + "/video"], "scanner.log", QT_ENV)
        elif act == "stop":
            _torch_off()
            _stop("scanner")
        else:
            return {"ok": False, "error": "action must be start or stop"}
    elif path == "/api/stop-all":
        _torch_off()
        _stop("scanner")
        _stop("camera")
    elif path == "/api/open-folder":
        appenv.open_folder(PROJECT)
        return {"ok": True}
    elif path == "/api/quit":           # "Quit" on the Home page: stop everything, then the app itself
        _torch_off()
        _stop("scanner")
        _stop("camera")
        threading.Timer(0.5, SERVER.shutdown).start()
        return {"ok": True, "message": "TCG Catalog has been closed. You can close this tab."}
    elif path == "/api/setup":
        return start_setup(req.get("kind", "seed"))
    elif path == "/api/backup":
        dest = f"collection.db.backup-{time.strftime('%Y%m%d-%H%M')}"
        shutil.copy(PROJECT / DB, PROJECT / dest)
        return {"ok": True, "message": f"Backed up to {dest}"}
    elif path == "/api/finish-batch":
        if pids_of("scanner"):
            _torch_off()
            _stop("scanner")
        r = subprocess.run(appenv.command("finish-batch"), cwd=PROJECT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180, **appenv.NO_WINDOW)
        msg = (r.stdout + r.stderr).strip() or "done"
        return {"ok": r.returncode == 0, "message": msg}
    else:
        return {"ok": False, "error": "unknown action"}
    return {"ok": True, **control_status()}


# ---------------------------------------------------------------------------
# First-run setup (Home page): the scanner needs the card catalog + recognition fingerprints.
#  * "seed"   -- load data/catalog_pokemon.json.gz shipped with the app (seconds, no internet)
#  * "update" -- download the latest catalog and fingerprint any new cards (needs internet;
#               only new sets take time -- a full build from scratch is ~30-40 min)
# Runs in a background thread; progress is reported through /api/status -> "setup".
# ---------------------------------------------------------------------------
import gzip, threading, re

SEED = appenv.APP_DIR / "data" / "catalog_pokemon.json.gz"
SETUP = {"running": False, "kind": None, "phase": "", "done": 0, "total": 0, "message": "", "error": None, "ok": None}


def setup_counts():
    db = sqlite3.connect(DB)
    core.schema(db)
    cards = db.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    hashed = db.execute("SELECT COUNT(*) FROM card_hashes").fetchone()[0]
    db.close()
    return {"catalog_cards": cards, "hashed_cards": hashed, "seed_available": SEED.exists(),
            "ready": hashed > 0, **{k: SETUP[k] for k in ("running", "kind", "phase", "done", "total", "message", "error", "ok")}}


def _setup_job(kind):
    SETUP.update(running=True, kind=kind, phase="Starting", done=0, total=0, message="", error=None, ok=None)
    try:
        db = sqlite3.connect(DB)
        core.schema(db)
        db.execute("INSERT OR IGNORE INTO games VALUES (?,?)", ("pokemon", "Pokémon TCG"))
        if kind == "seed":
            SETUP.update(phase="Reading the bundled card data")
            with gzip.open(SEED, "rt", encoding="utf-8") as f:
                data = json.load(f)
            db.executemany("INSERT OR IGNORE INTO sets (id, game_id, name, series, release_date, printed_total, total) VALUES (?,?,?,?,?,?,?)", data["sets"])
            cards, hashes = data["cards"], data["hashes"]
            SETUP.update(phase="Adding cards", total=len(cards) + len(hashes))
            for k in range(0, len(cards), 2000):
                for c in cards[k:k + 2000]:
                    core.add_card(db, *c)
                db.commit()
                SETUP.update(done=min(k + 2000, len(cards)))
            SETUP.update(phase="Adding recognition fingerprints")
            for k in range(0, len(hashes), 4000):
                db.executemany("INSERT OR IGNORE INTO card_hashes VALUES (?,?,?)", hashes[k:k + 4000])
                db.commit()
                SETUP.update(done=len(cards) + min(k + 4000, len(hashes)))
            SETUP.update(message=f"Installed {len(cards):,} cards and {len(hashes):,} fingerprints.")
        else:
            import catalog_sync, build_hash_index
            SETUP.update(phase="Downloading the latest card list")
            def on_cat(msg):
                m = re.search(r"(\d+)/(\d+)", msg)
                if m:
                    SETUP.update(done=int(m.group(1)), total=int(m.group(2)))
                SETUP.update(message=msg)
            catalog_sync.sync_pokemon(db, on_progress=on_cat)
            SETUP.update(phase="Fingerprinting new cards", done=0, total=0)
            n = build_hash_index.build(db, on_progress=lambda i, total, msg: SETUP.update(done=i, total=total, message=msg))
            SETUP.update(message=f"Catalog updated; {n} new card(s) fingerprinted.")
        db.close()
        SETUP.update(ok=True, phase="Done")
        if pids_of("scanner"):   # the scanner loads fingerprints at start-up: restart it to pick up new ones
            _stop("scanner")
            _spawn("scanner", [CAM_URL + "/video"], "scanner.log", QT_ENV)
    except Exception as e:
        SETUP.update(ok=False, error=repr(e), phase="Failed")
    finally:
        SETUP.update(running=False)


def start_setup(kind):
    if SETUP["running"]:
        return {"ok": False, "error": "setup is already running"}
    if kind == "seed" and not SEED.exists():
        return {"ok": False, "error": "bundled card data (data/catalog_pokemon.json.gz) is missing -- use Update instead"}
    threading.Thread(target=_setup_job, args=(kind,), daemon=True).start()
    return {"ok": True}


def phone_qr_svg():
    """QR code of the phone page address, so the phone can just scan it with its camera."""
    try:
        import qrcode, qrcode.image.svg
    except ImportError:
        return None
    img = qrcode.make(f"https://{lan_ip()}:8443", image_factory=qrcode.image.svg.SvgPathFillImage, box_size=10, border=2)
    return img.to_string()


def log_tail(name, n=14):
    f = PROJECT / {"scanner": "scanner.log", "camera": "phone_cam.log"}.get(name, "scanner.log")
    try:
        lines = [l for l in f.read_text(errors="ignore").splitlines() if "QFont" not in l and "Qt no longer" not in l and "mjpeg @" not in l]
    except OSError:
        lines = []
    return {"lines": lines[-n:]}


def build_data(db):
    # Cards from already-sorted earlier batches (loaded by add_prior_batch.py).
    # Separate table, so it survives "clear the db"; counts as already owned, so a
    # card scanned now that exists in an earlier batch is flagged as a duplicate.
    db.execute("""CREATE TABLE IF NOT EXISTS prior_collection (
        card_id TEXT, variant TEXT, condition TEXT, language TEXT,
        qty INTEGER NOT NULL DEFAULT 0, batch TEXT,
        PRIMARY KEY (card_id, variant, condition, language, batch))""")
    prior = {tuple(r) for r in db.execute(
        "SELECT card_id, variant, condition, language FROM prior_collection")}
    rows = db.execute("""
        SELECT sl.id, sl.card_id, sl.raw_name, sl.raw_number, sl.raw_variant, sl.variant, sl.qty,
               sl.reason, sl.condition, sl.language,
               s.name AS set_name, c.image_url, c.name AS resolved_name, g.name AS game_name
        FROM scan_log sl
        LEFT JOIN cards c ON c.id = sl.card_id
        LEFT JOIN sets s ON s.id = c.set_id
        LEFT JOIN games g ON g.id = sl.game_id
        ORDER BY sl.id""").fetchall()
    cards = []
    seen = set()  # (card_id, variant, condition, language) already placed once -> any further one is a duplicate
    for (scan_id, card_id, raw_name, number, raw_variant, variant, qty, reason,
         condition, language, set_name, image_url, resolved_name, game_name) in rows:
        row_qty_gt1 = bool(qty and qty > 1)  # this one scan already represents >1 physical copy
        is_duplicate = row_qty_gt1
        in_prior = False
        if card_id:
            key = (card_id, variant, condition, language)
            in_prior = key in prior
            is_duplicate = is_duplicate or key in seen or in_prior
            seen.add(key)
        # Known Delver quirk: a qty>1 row with no Foil value can hide a normal+foil
        # pair collapsed into one row (see Known caveats in CLAUDE.md) -- flag it
        # specifically so it gets checked by eye instead of assumed "2 normals".
        check_foil = row_qty_gt1 and not (raw_variant or "").strip()
        cards.append({
            "id": scan_id,
            "card_id": card_id,
            "name": resolved_name or raw_name,
            "number": number,
            "variant": raw_variant,
            "norm_variant": variant,
            "qty": qty,
            "check_foil": check_foil,
            "set": set_name,
            "game": game_name,
            "image": image_url,
            "needs_review": set_name is None,
            "reason": reason,
            "duplicate": is_duplicate,
            "in_prior_batch": in_prior,
        })
    return cards

def do_ingest(path, on_event=lambda e: None):
    """Ingest one CSV (already on disk at `path`), regenerate all out/ views and
    viewer_data.json. Shared by the upload endpoint and the pick-existing-file
    endpoint so both go through the same logic. `on_event` is called with a dict
    per step (sets sync start/done, each card start/done) for live progress."""
    db = sqlite3.connect(DB)
    core.schema(db)
    db.execute("INSERT OR IGNORE INTO games VALUES (?,?)", ("pokemon", "Pokémon TCG"))
    db.commit()
    try:
        if not db.execute("SELECT 1 FROM sets WHERE game_id='pokemon' LIMIT 1").fetchone():
            ing.sync_sets(db, on_event)
        ing.ingest(db, str(path), on_event)
        core.report(db)
        cards = build_data(db)
        Path("viewer_data.json").write_text(json.dumps(cards))
        return {"ok": True, "count": len(cards)}
    except SystemExit as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": repr(e)}

def write_collection_snapshot():
    """Write out/collection_current.csv: the merged collection (current scan + every
    finished batch) as plain rows, rewritten after every change. collection.db is the
    source of truth; this is the human-readable copy of it."""
    import csv
    db = _owned_db()
    rows = db.execute(f"""
        SELECT s.name, c.number, c.name, c.rarity, o.cur + o.prev, o.cur, o.prev
        FROM ({OWNED_SQL}) o JOIN cards c ON c.id = o.card_id LEFT JOIN sets s ON s.id = c.set_id
        ORDER BY s.release_date, s.name""").fetchall()
    db.close()
    rows.sort(key=lambda r: (r[0] or "", [int(t) if t.isdigit() else t for t in __import__("re").split(r"(\d+)", r[1] or "")]))
    Path("out").mkdir(exist_ok=True)
    with open("out/collection_current.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["set", "number", "name", "rarity", "qty", "in_current_scan", "in_earlier_batches"])
        w.writerows(rows)
    return len(rows)


_backed_up = False


def do_remove_from_collection(card_id):
    """Remove ONE copy of a card from the collection (false positive found while
    sorting/browsing). Takes it from the current scan if it's there (newest scan row,
    same reversal as the sorter's delete button), otherwise from the newest earlier
    batch in prior_collection. Backs up collection.db once per server run first."""
    global _backed_up
    import shutil, time
    try:
        if not _backed_up:
            shutil.copy(DB, f"{DB}.pre-remove-{time.strftime('%Y%m%d-%H%M')}")
            _backed_up = True
        db = _owned_db()
        row = db.execute("SELECT id FROM scan_log WHERE card_id = ? ORDER BY id DESC LIMIT 1", (card_id,)).fetchone()
        if row:
            db.close()
            res = do_delete(row[0])
            write_collection_snapshot()
            return {**res, "from": "this scan"} if res.get("ok") else res
        row = db.execute("""SELECT variant, condition, language, batch, qty FROM prior_collection
                            WHERE card_id = ? ORDER BY batch DESC LIMIT 1""", (card_id,)).fetchone()
        if not row:
            db.close()
            return {"ok": False, "error": "card not in the collection"}
        variant, condition, language, batch, qty = row
        key = (card_id, variant, condition, language, batch)
        if qty > 1:
            db.execute("UPDATE prior_collection SET qty = qty - 1 WHERE card_id=? AND variant=? AND condition=? AND language=? AND batch=?", key)
        else:
            db.execute("DELETE FROM prior_collection WHERE card_id=? AND variant=? AND condition=? AND language=? AND batch=?", key)
        db.commit()
        db.close()
        write_collection_snapshot()
        return {"ok": True, "from": batch}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


def do_delete(scan_id):
    """Remove one false-positive scan: undo its collection qty, drop it from
    scan_log, regenerate out/ views and viewer_data.json."""
    db = sqlite3.connect(DB)
    try:
        if not core.delete_scan_entry(db, scan_id):
            return {"ok": False, "error": f"scan id {scan_id} not found"}
        core.report(db)
        cards = build_data(db)
        Path("viewer_data.json").write_text(json.dumps(cards))
        return {"ok": True, "count": len(cards)}
    except Exception as e:
        return {"ok": False, "error": repr(e)}

def do_set_variant(scan_id, variant):
    """Owner hand-flags a card's variant in the viewer (Delver's Foil column
    is unreliable). The viewer's buttons send Pokemon's own raw labels
    ("regular"/"holofoil"/"reverse holofoil") -- normalize through
    ingest_delver's vocabulary before it reaches the game-agnostic core
    (collection_core.set_variant expects an already-normalized value).
    Moves the collection qty to the new variant key, regenerates out/ views
    and viewer_data.json."""
    db = sqlite3.connect(DB)
    try:
        if not core.set_variant(db, scan_id, ing.norm_variant(variant)):
            return {"ok": False, "error": f"scan id {scan_id} not found"}
        core.report(db)
        cards = build_data(db)
        Path("viewer_data.json").write_text(json.dumps(cards))
        return {"ok": True, "count": len(cards)}
    except Exception as e:
        return {"ok": False, "error": repr(e)}

def local_csvs():
    """CSVs sitting directly in the project folder -- e.g. dropped there by a
    phone sync tool -- so they can be picked without an OS file browser."""
    return sorted(p.name for p in Path(".").glob("*.csv"))

class Handler(SimpleHTTPRequestHandler):
    # HTTP/1.1 is needed for chunked Transfer-Encoding (live ingest progress),
    # but this server is single-threaded/single-connection (plain HTTPServer,
    # not ThreadingHTTPServer) -- HTTP/1.1's default keep-alive would let one
    # idle connection block the accept loop forever, hanging every other
    # request (confirmed live: a browser tab left on an old page wedged the
    # whole server, including unrelated curl requests). Forcing close on every
    # response keeps HTTP/1.1 (for chunked framing) without keep-alive's
    # single-threaded hazard.
    protocol_version = "HTTP/1.1"

    # Files the pages poll, written into the data folder by the scanner/server; everything else
    # is a page, script or image from the app folder. Nothing else (the database, certificates,
    # logs) is ever served -- this server is reachable from the LAN.
    RUNTIME_FILES = {"live_cam.jpg", "live_frame.jpg", "live_status.json", "viewer_data.json", "background.jpg"}
    STATIC_TYPES = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".ico", ".webp", ".woff2"}

    def translate_path(self, path):
        from urllib.parse import urlparse, unquote
        rel = unquote(urlparse(path).path).lstrip("/") or "index.html"
        if rel in self.RUNTIME_FILES:
            return str(PROJECT / rel)
        app = appenv.APP_DIR.resolve()
        p = (app / rel).resolve()
        if p.suffix.lower() in self.STATIC_TYPES and app in p.parents and p.is_file():
            return str(p)
        return str(app / "__missing__")

    def end_headers(self):
        # Never let the browser reuse an old copy of a page/script (stale cached JS caused confusing breakage)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _send_json(self, obj):
        payload = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def _stream_ingest(self, path):
        """Run do_ingest, writing each progress event as one NDJSON line over a
        chunked response as it happens (not buffered until the end), so the
        browser can show live per-card progress instead of one final result."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(obj):
            try:
                data = (json.dumps(obj) + "\n").encode()
                self.wfile.write(f"{len(data):x}\r\n".encode())
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # client navigated away mid-ingest -- let ingest finish server-side

        result = do_ingest(path, on_event=emit)
        emit({**result, "event": "finished"})
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self.close_connection = True  # see note on protocol_version above
        if self.path == "/list-csv":
            self._send_json(local_csvs()); return
        if self.path.startswith("/owned-sets"):
            self._send_json(owned_sets()); return
        if self.path.startswith("/api/status"):
            self._send_json(control_status()); return
        if self.path.startswith("/api/phone-qr.svg"):
            svg = phone_qr_svg()
            if svg is None:
                self.send_error(404, "qrcode package not installed"); return
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(svg)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(svg); return
        if self.path.startswith("/api/log"):
            from urllib.parse import urlparse, parse_qs
            self._send_json(log_tail(parse_qs(urlparse(self.path).query).get("name", ["scanner"])[0])); return
        if self.path.startswith("/card-finishes"):
            from urllib.parse import urlparse, parse_qs
            self._send_json(card_finishes(parse_qs(urlparse(self.path).query).get("id", [""])[0])); return
        if self.path.startswith("/search-cards"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send_json(search_cards(q.get("q", [""])[0], q.get("missing", ["0"])[0] == "1")); return
        if self.path.startswith("/set-cards"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send_json(set_cards(q.get("id", [""])[0], q.get("missing", ["0"])[0] == "1")); return
        super().do_GET()

    def do_POST(self):
        self.close_connection = True  # see note on protocol_version above
        if self.path == "/upload":
            filename = self.headers.get("X-Filename", "upload.csv")
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            UPLOAD_DIR.mkdir(exist_ok=True)
            path = UPLOAD_DIR / (Path(filename).name or "upload.csv")
            path.write_bytes(body)
            self._stream_ingest(path)
        elif self.path == "/ingest-local":
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            name = Path(req.get("filename", "")).name
            path = Path(name)
            if not name or not path.is_file():
                self._send_json({"ok": False, "error": f"{name!r} not found"}); return
            self._stream_ingest(path)
        elif self.path == "/delete-card":
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            scan_id = req.get("id")
            if not isinstance(scan_id, int):
                self._send_json({"ok": False, "error": "missing/invalid id"}); return
            self._send_json(do_delete(scan_id))
        elif self.path.startswith("/api/") and self.path != "/api/status":
            if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                self.send_error(403, "control actions are only allowed from this computer"); return
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            try:
                self._send_json(control_action(self.path, req))
            except Exception as e:
                self._send_json({"ok": False, "error": repr(e)})
        elif self.path == "/remove-from-collection":
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(req.get("card_id"), str):
                self._send_json({"ok": False, "error": "missing card_id"}); return
            self._send_json(do_remove_from_collection(req["card_id"]))
        elif self.path == "/set-variant":
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            scan_id = req.get("id")
            variant = req.get("variant")
            if not isinstance(scan_id, int) or not isinstance(variant, str):
                self._send_json({"ok": False, "error": "missing/invalid id or variant"}); return
            self._send_json(do_set_variant(scan_id, variant))
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        pass  # quiet -- avoid noisy stderr for every GET during spacebar use

def main():
    appenv.enter_data_dir()   # launchers may start us from anywhere; every runtime path is data-folder-relative
    _db = sqlite3.connect(DB)              # first run: create the database and its tables
    core.schema(_db)
    _db.execute("INSERT OR IGNORE INTO games VALUES (?,?)", ("pokemon", "Pokémon TCG"))
    _db.commit(); _db.close()
    if not Path("viewer_data.json").exists():
        Path("viewer_data.json").write_text("[]")
    if not setup_counts()["ready"] and SEED.exists():   # first start: load the bundled cards right away
        start_setup("seed")
    port = free_port()
    print(f"Serving at http://localhost:{port}/ -- control panel on the home page.", flush=True)
    (PROJECT / ".sort_viewer_port").write_text(str(port))  # lets the launcher find a running instance
    write_collection_snapshot()
    if "--no-browser" not in sys.argv:
        appenv.open_url(f"http://localhost:{port}/")
    # 0.0.0.0, not localhost -- so the CSV-upload UI also works from a phone on the LAN.
    global SERVER
    SERVER = HTTPServer(("0.0.0.0", port), Handler)
    try:
        SERVER.serve_forever()
    finally:
        (PROJECT / ".sort_viewer_port").unlink(missing_ok=True)


SERVER = None


if __name__ == "__main__":
    main()
