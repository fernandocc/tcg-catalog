#!/usr/bin/env python3
"""
Ingest a Poke Delver CSV export into the shared collection_core schema
(game_id='pokemon'), merge duplicates, and export per-set sorted views.

This module only holds what's genuinely Delver/CSV-specific: header-name
mapping (export presets vary), Pokemon's own variant vocabulary, fuzzy
set-name resolution, and the Creation-Date re-sort. Everything generic
(schema, card/scan upserts, delete, variant-move, reporting) lives in
collection_core.py and is shared with every other game's importer and with
live_scanner.py.

Design note (2026-10-03): this does ONE bulk network call ever (sync_sets,
a fallback if catalog_sync.py hasn't been run yet) and NO per-card API calls.
Delver already identified each card when you scanned it -- its CSV export
already has name/set/number/rarity/price -- so there's nothing to look up
per card. Card images are fetched from a predictable CDN URL pattern
(https://images.scrydex.com/pokemon/<set_id>-<number>/small) instead of the
(very flaky) per-card JSON endpoint.

Usage:
  python ingest_delver.py batch01.csv batch02.csv ...   (any number of files)
  python ingest_delver.py --report     # re-export without importing

Outputs:
  collection.db              SQLite (shared multi-TCG schema, see collection_core.py)
  out/collection_by_set.csv  every card, sorted release date -> set -> number
  out/duplicates.csv         anything with qty > 1 (trade/sell pile)
  out/sets/<Set Name>.csv    one file per set, with owned/missing counts
  out/sort_manifest.csv      every row in the ORIGINAL scan order (not resorted) --
                             walk your physically-scanned stack in this same order
                             and place each card into the listed set bin. Rows the
                             API couldn't resolve are marked NEEDS REVIEW instead of
                             a set, so they don't get sorted into the wrong bin.
"""
import csv, os, re, sqlite3, sys, time
from pathlib import Path
import requests

import collection_core as core

DB = "collection.db"
GAME = "pokemon"
API = "https://api.pokemontcg.io/v2"
HDR = {"X-Api-Key": os.environ.get("POKEMONTCG_API_KEY", "")}
IMAGE_BASE = "https://images.scrydex.com/pokemon"

# Delver's column names vary by export preset. Each key lists accepted headers
# (case-insensitive). Run once, check the printed header row, add yours if missing.
COLS = {
    "name":      ["name", "card name", "card"],
    "set":       ["set", "set name", "edition", "expansion"],
    "set_code":  ["set code", "setcode", "set id", "code"],
    "number":    ["number", "card number", "collector number", "no", "#"],
    "qty":       ["quantity", "qty", "count", "amount"],
    "variant":   ["foil", "finish", "printing", "variant", "treatment"],
    "condition": ["condition", "cond"],
    "language":  ["language", "lang"],
    "unit_price": ["unit price", "price"],
    "rarity":    ["rarity"],
    "created":   ["creation date", "date created", "date added", "scanned at", "scan date"],
}

def parse_price(s):
    s = re.sub(r"[^0-9.]", "", s or "")
    return float(s) if s else None

def get(path, **params):
    # The API is bursty-flaky (observed ~20% 5xx/502 rate during outages, not just
    # isolated blips), so this retries more than a typical transient-error guard
    # would, with a capped backoff rather than ever-growing sleeps.
    for attempt in range(8):
        r = requests.get(f"{API}/{path}", headers=HDR, params=params, timeout=30)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(5 * (attempt + 1), 20)); continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()

def sync_sets(db, on_event=lambda e: None):
    """Fallback only -- prefer `catalog_sync.py pokemon` (free static mirror,
    full card catalog too, not just sets) run ahead of time. This keeps CSV
    ingest working standalone even if that hasn't happened yet."""
    on_event({"event": "sets_start"})
    sets = get("sets", pageSize=250)["data"]
    for s in sets:
        db.execute("INSERT OR IGNORE INTO sets VALUES (?,?,?,?,?,?,?)",
                   (f"{GAME}:{s['id']}", GAME, s["name"], s["series"], s["releaseDate"],
                    s.get("printedTotal", s.get("total")), s["total"]))
    db.commit()
    on_event({"event": "sets_done", "count": len(sets)})

def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def norm_variant(v):
    """Pokemon's own variant vocabulary -- not shared with other games."""
    v = (v or "").lower()
    if "reverse" in v: return "reverse"
    if "master" in v: return "masterball"     # Prismatic Evolutions / SV-era patterns
    if "poke" in v and "ball" in v: return "pokeball"
    if "holo" in v or v in ("foil", "yes", "true", "1"): return "holo"
    if v == "regular": return "normal"
    return "normal"

def map_headers(header):
    low = {h.strip().lower(): h for h in header}
    return {k: next((low[a] for a in al if a in low), None) for k, al in COLS.items()}

def resolve_set(db, raw_set, raw_code):
    """Fuzzy-match a CSV set name/code against this game's known sets."""
    if raw_code:
        row = db.execute("SELECT id FROM sets WHERE game_id=? AND lower(id)=?",
                          (GAME, f"{GAME}:{raw_code}".lower())).fetchone()
        if row: return row[0]
    target = norm(raw_set)
    for sid, name in db.execute("SELECT id, name FROM sets WHERE game_id=?", (GAME,)):
        if norm(name) == target: return sid
    return None

def local_card(db, set_id, number, name, rarity):
    """Register a card from the CSV's own data -- no API call. `number` only
    needs normalizing (strip leading zeros / the printed_total suffix) to
    build the same id scheme the API uses, which is also the scrydex image
    CDN's id scheme, so the image URL can be constructed directly."""
    number = number.split("/")[0].lstrip("0") or "0"   # "025/165" -> "25"
    local_set_id = set_id.split(":", 1)[1] if ":" in set_id else set_id
    card_id = f"{GAME}:{local_set_id}-{number}"
    image_url = f"{IMAGE_BASE}/{local_set_id}-{number}/small"
    core.add_card(db, card_id, GAME, set_id, name, number, rarity, image_url)
    return card_id

def ingest(db, path, on_event=lambda e: None):
    import hashlib
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if db.execute("SELECT 1 FROM imports WHERE sha=?", (sha,)).fetchone():
        print(f"Skipping {path}: identical file already imported")
        on_event({"event": "skip", "reason": "already imported"})
        return
    with open(path, newline="", encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        cols = map_headers(rdr.fieldnames)
        print("Header mapping:", cols)
        if not cols["name"] or not (cols["set"] or cols["set_code"]) or not cols["number"]:
            sys.exit(f"Need name, set, number columns. Found: {rdr.fieldnames}")
        rows = list(rdr)
        # Delver's CSV row order does NOT reliably reflect physical scan order
        # (confirmed 2026-10-03: a same-day 59-card batch came back in an order
        # unrelated to actual scan sequence, even with Delver's own "sort by
        # scan date" export option -- that option only has day granularity).
        # A "Creation Date" column with real per-card timestamps (ISO, sorts
        # correctly as a string) is the only reliable source -- use it to
        # reorder rows before processing if present, instead of trusting
        # whatever order the file happens to list them in.
        if cols["created"]:
            rows.sort(key=lambda r: r.get(cols["created"]) or "")
        total = len(rows)
        added = 0
        for i, row in enumerate(rows, 1):
            g = lambda k: (row.get(cols[k]) or "").strip() if cols[k] else ""
            qty = int(g("qty") or 1)
            on_event({"event": "card_start", "index": i, "total": total, "name": g("name")})
            set_id = resolve_set(db, g("set"), g("set_code"))
            card_id = local_card(db, set_id, g("number"), g("name"), g("rarity")) if set_id else None
            created = g("created")
            if not card_id:
                reason = "set not found"
                db.execute("INSERT INTO unmatched VALUES (?,?,?,?,?,?)",
                           (GAME, g("name"), g("set"), g("number"), qty, reason))
                core.upsert_scan(db, GAME, "delver-csv", created, None, g("name"), g("number"),
                                  g("variant"), norm_variant(g("variant")), qty, None, reason,
                                  g("condition") or "NM", g("language") or "English")
                on_event({"event": "card_done", "index": i, "total": total, "name": g("name"),
                          "status": "unmatched", "reason": reason})
                continue
            variant = norm_variant(g("variant"))
            condition = g("condition") or "NM"
            language = g("language") or "English"
            action = core.upsert_scan(db, GAME, "delver-csv", created, card_id, g("name"), g("number"),
                                        g("variant"), variant, qty, g("set"), None, condition, language)
            if action == "inserted":
                db.execute("""INSERT INTO collection VALUES (?,?,?,?,?)
                    ON CONFLICT(card_id, variant, condition, language)
                    DO UPDATE SET qty = qty + excluded.qty""",
                    (card_id, variant, condition, language, qty))
            core.backfill_price(db, card_id, variant, parse_price(g("unit_price")))
            added += qty
            on_event({"event": "card_done", "index": i, "total": total, "name": g("name"),
                      "status": "ok", "set": g("set"), "action": action})
        db.execute("INSERT INTO imports (sha, filename) VALUES (?,?)", (sha, path))
        db.commit()
        print(f"Ingested {added} cards from {path}")

if __name__ == "__main__":
    db = sqlite3.connect(DB); core.schema(db)
    db.execute("INSERT OR IGNORE INTO games VALUES (?,?)", (GAME, "Pokémon TCG"))
    db.commit()
    if not db.execute("SELECT 1 FROM sets WHERE game_id=? LIMIT 1", (GAME,)).fetchone() or "--sync-sets" in sys.argv:
        sync_sets(db)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for a in args: ingest(db, a)
    core.report(db)
