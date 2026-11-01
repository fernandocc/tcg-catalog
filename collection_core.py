#!/usr/bin/env python3
"""
Generic, game-agnostic collection core -- shared by every TCG importer
(ingest_delver.py for Pokemon/Delver CSV, future ingest_optcg.py/ingest_gcg.py,
and live_scanner.py for camera-based recognition) plus sort_viewer.py.

No CSV-quirk handling and no webhook anything lives here on purpose -- that
stuff is specific to one import *source*, not to the collection itself.
Each importer normalizes its own raw data (header names, variant vocabulary,
set-name fuzzy matching, etc.) down to a clean call into the functions below.

id scheme: cards.id / sets.id are prefixed "<game_id>:<original-id>"
(e.g. "pokemon:base1-1") so ids stay globally unique across games. Image
URLs still use the *unprefixed* set_id/number, since that's what the CDN
pattern expects -- only the database primary keys carry the game prefix.
"""
from pathlib import Path

DB = "collection.db"

def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS games (id TEXT PRIMARY KEY, name TEXT);
    CREATE TABLE IF NOT EXISTS sets (
        id TEXT PRIMARY KEY, game_id TEXT, name TEXT, series TEXT,
        release_date TEXT, printed_total INTEGER, total INTEGER);
    CREATE TABLE IF NOT EXISTS cards (
        id TEXT PRIMARY KEY, game_id TEXT, set_id TEXT, name TEXT, number TEXT,
        rarity TEXT, tcg_market_normal REAL, tcg_market_holo REAL,
        tcg_market_reverse REAL, price_updated TEXT, image_url TEXT);
    -- One row per distinct physical version. Duplicates = qty > 1.
    CREATE TABLE IF NOT EXISTS collection (
        card_id TEXT, variant TEXT, condition TEXT, language TEXT,
        qty INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (card_id, variant, condition, language));
    CREATE TABLE IF NOT EXISTS imports (sha TEXT PRIMARY KEY, filename TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS unmatched (
        game_id TEXT, raw_name TEXT, raw_set TEXT, raw_number TEXT, qty INTEGER, reason TEXT);
    -- One row per physical scan event (any source: CSV, live camera, future
    -- game APIs), in the order it happened. Drives the sort manifest and the
    -- per-card viewer -- walk your physically-scanned stack in this order.
    -- raw_variant: original text from the source (e.g. CSV's Foil column, can
    -- be blank) -- kept for display/quirk detection. variant: normalized
    -- value actually used as the `collection` bookkeeping key.
    CREATE TABLE IF NOT EXISTS scan_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, source TEXT,
        card_id TEXT, raw_name TEXT, raw_number TEXT, raw_variant TEXT, variant TEXT,
        qty INTEGER, set_name TEXT, reason TEXT, condition TEXT, language TEXT, created TEXT);
    -- One row per card once its reference image has been hashed (any game).
    CREATE TABLE IF NOT EXISTS card_hashes (card_id TEXT PRIMARY KEY, phash TEXT, dhash TEXT);
    """)
    db.commit()

def add_card(db, card_id, game_id, set_id, name, number, rarity, image_url):
    """Register a card if not already known. INSERT OR IGNORE -- never
    clobbers an existing row (e.g. one with real scanned-in prices)."""
    db.execute("""INSERT OR IGNORE INTO cards
        (id, game_id, set_id, name, number, rarity, tcg_market_normal,
         tcg_market_holo, tcg_market_reverse, price_updated, image_url)
        VALUES (?,?,?,?,?,?,NULL,NULL,NULL,NULL,?)""",
        (card_id, game_id, set_id, name, number, rarity, image_url))

def backfill_price(db, card_id, variant, price):
    """Set this card+variant's price if not already set -- first scan wins."""
    col = {"normal": "tcg_market_normal", "holo": "tcg_market_holo",
           "reverse": "tcg_market_reverse"}.get(variant)
    if col is None or price is None:
        return
    db.execute(f"UPDATE cards SET {col} = ? WHERE id = ? AND {col} IS NULL", (price, card_id))

def _remove_collection_contribution(db, card_id, variant, qty, condition, language):
    if not card_id:
        return
    db.execute("""UPDATE collection SET qty = qty - ?
                  WHERE card_id=? AND variant=? AND condition=? AND language=?""",
               (qty, card_id, variant, condition, language))
    db.execute("""DELETE FROM collection
                  WHERE card_id=? AND variant=? AND condition=? AND language=? AND qty<=0""",
               (card_id, variant, condition, language))

def upsert_scan(db, game_id, source, created, card_id, raw_name, raw_number,
                raw_variant, variant, qty, set_name, reason, condition, language):
    """Insert a new scan_log row, unless `created` (a stable per-physical-scan
    id) matches a row already in scan_log -- in that case update in place:
    reverse the old `collection` contribution and apply the new one, rather
    than double-counting. `variant` must already be normalized by the caller
    (each game/importer has its own vocabulary); `raw_variant` is the original
    source text, kept only for display/quirk detection. Returns "updated" or "inserted"."""
    existing = db.execute(
        "SELECT id, card_id, variant, qty, condition, language FROM scan_log WHERE created=? AND created IS NOT NULL AND created != ''",
        (created,)).fetchone() if created else None
    if existing:
        old_id, old_card_id, old_variant, old_qty, old_condition, old_language = existing
        _remove_collection_contribution(db, old_card_id, old_variant, old_qty, old_condition, old_language)
        if card_id:
            db.execute("""INSERT INTO collection VALUES (?,?,?,?,?)
                ON CONFLICT(card_id, variant, condition, language)
                DO UPDATE SET qty = qty + excluded.qty""",
                (card_id, variant, condition, language, qty))
        db.execute("""UPDATE scan_log SET game_id=?, source=?, card_id=?, raw_name=?, raw_number=?,
                      raw_variant=?, variant=?, qty=?, set_name=?, reason=?, condition=?, language=? WHERE id=?""",
                   (game_id, source, card_id, raw_name, raw_number, raw_variant, variant, qty, set_name, reason, condition, language, old_id))
        return "updated"
    db.execute("""INSERT INTO scan_log
        (game_id, source, card_id, raw_name, raw_number, raw_variant, variant, qty, set_name, reason, condition, language, created)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (game_id, source, card_id, raw_name, raw_number, raw_variant, variant, qty, set_name, reason, condition, language, created))
    return "inserted"

def delete_scan_entry(db, scan_id):
    """Remove one scanned row (false positive from any source). Reverses
    exactly what it contributed to `collection`, then drops the scan_log row."""
    row = db.execute("SELECT card_id, variant, qty, condition, language FROM scan_log WHERE id=?",
                      (scan_id,)).fetchone()
    if not row:
        return False
    card_id, variant, qty, condition, language = row
    _remove_collection_contribution(db, card_id, variant, qty, condition, language)
    db.execute("DELETE FROM scan_log WHERE id=?", (scan_id,))
    db.commit()
    return True

def set_variant(db, scan_id, new_variant):
    """Hand-flag a scan's variant (e.g. Regular/Holofoil/Reverse Holofoil in
    the viewer). Moves this scan row's `collection` contribution from its old
    variant key to the new one."""
    row = db.execute("SELECT card_id, variant, qty, condition, language FROM scan_log WHERE id=?",
                      (scan_id,)).fetchone()
    if not row:
        return False
    card_id, old_variant, qty, condition, language = row
    if old_variant == new_variant:
        return True
    _remove_collection_contribution(db, card_id, old_variant, qty, condition, language)
    if card_id:
        db.execute("""INSERT INTO collection VALUES (?,?,?,?,?)
            ON CONFLICT(card_id, variant, condition, language)
            DO UPDATE SET qty = qty + excluded.qty""",
            (card_id, new_variant, condition, language, qty))
    db.execute("UPDATE scan_log SET raw_variant=?, variant=? WHERE id=?", (new_variant, new_variant, scan_id))
    db.commit()
    return True

def set_card(db, scan_id, new_card_id):
    """Re-point a scan at a different catalog card (e.g. the right One Piece printing
    when the camera can't tell a reprint from the original). Moves this scan row's
    `collection` contribution to the new card, same reversal pattern as set_variant."""
    row = db.execute("SELECT card_id, variant, qty, condition, language FROM scan_log WHERE id=?",
                      (scan_id,)).fetchone()
    if not row:
        return False
    card_id, variant, qty, condition, language = row
    if card_id == new_card_id:
        return True
    _remove_collection_contribution(db, card_id, variant, qty, condition, language)
    db.execute("""INSERT INTO collection VALUES (?,?,?,?,?)
        ON CONFLICT(card_id, variant, condition, language)
        DO UPDATE SET qty = qty + excluded.qty""",
        (new_card_id, variant, condition, language, qty))
    name = db.execute("SELECT name FROM cards WHERE id=?", (new_card_id,)).fetchone()
    db.execute("UPDATE scan_log SET card_id=?, raw_name=COALESCE(?, raw_name) WHERE id=?",
               (new_card_id, name[0] if name else None, scan_id))
    db.commit()
    return True

def ingest_live_scan(db, card_id, variant="normal", condition="NM", language="English"):
    """Commit one card identified by the live camera scanner. The card is
    already fully known (card_id came straight from the hash match against
    the catalog) -- no set/name resolution needed, unlike the CSV path."""
    import datetime
    row = db.execute("SELECT game_id, name FROM cards WHERE id=?", (card_id,)).fetchone()
    game_id, name = row if row else (None, None)
    created = f"live:{datetime.datetime.now().isoformat()}"
    action = upsert_scan(db, game_id, "live-camera", created, card_id, name, None,
                         variant, variant, 1, None, None, condition, language)
    if action == "inserted":
        db.execute("""INSERT INTO collection VALUES (?,?,?,?,?)
            ON CONFLICT(card_id, variant, condition, language)
            DO UPDATE SET qty = qty + excluded.qty""",
            (card_id, variant, condition, language, 1))
    db.commit()
    return action

def report(db):
    import csv, re
    out = Path("out"); (out / "sets").mkdir(parents=True, exist_ok=True)
    q = """
    SELECT g.name, s.release_date, s.series, s.name, c.number, c.name, c.rarity,
           col.variant, col.condition, col.qty,
           CASE col.variant WHEN 'reverse' THEN c.tcg_market_reverse
                            WHEN 'holo' THEN c.tcg_market_holo
                            ELSE COALESCE(c.tcg_market_normal, c.tcg_market_holo) END AS price
    FROM collection col JOIN cards c ON c.id = col.card_id
    JOIN sets s ON s.id = c.set_id
    LEFT JOIN games g ON g.id = c.game_id
    ORDER BY g.name, s.release_date, s.name,
             CAST(c.number AS INTEGER), c.number, col.variant"""
    rows = db.execute(q).fetchall()
    hdr = ["Game", "Release", "Series", "Set", "No", "Name", "Rarity",
           "Variant", "Cond", "Qty", "Unit $", "Total $"]
    fmt = lambda r: list(r) + [round((r[10] or 0) * r[9], 2)]

    with open(out / "collection_by_set.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(hdr); w.writerows(fmt(r) for r in rows)
    with open(out / "duplicates.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(hdr + ["Extras"])
        w.writerows(fmt(r) + [r[9] - 1] for r in rows if r[9] > 1)

    by_set = {}
    for r in rows: by_set.setdefault(r[3], []).append(r)
    for set_name, rs in by_set.items():
        total_row = db.execute("SELECT printed_total FROM sets WHERE name=?", (set_name,)).fetchone()
        total = total_row[0] if total_row else None
        owned = len({r[4] for r in rs})
        safe = re.sub(r'[\\/:*?"<>|]', "_", set_name)
        with open(out / "sets" / f"{safe}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([f"{set_name}: {owned}/{total} unique numbers owned"])
            w.writerow(hdr); w.writerows(fmt(r) for r in rs)

    val = sum((r[10] or 0) * r[9] for r in rows)
    dup = sum(r[9] - 1 for r in rows if r[9] > 1)
    um = db.execute("SELECT COUNT(*) FROM unmatched").fetchone()[0]
    print(f"{sum(r[9] for r in rows)} cards | {len(by_set)} sets | "
          f"{dup} duplicate copies | ~${val:,.2f} | {um} unmatched (see table)")

    write_sort_manifest(db, out)

def write_sort_manifest(db, out):
    import csv
    rows = db.execute("""
        SELECT sl.source, sl.raw_name, sl.raw_number, sl.raw_variant, sl.qty, sl.reason,
               s.name AS canonical_set
        FROM scan_log sl
        LEFT JOIN cards c ON c.id = sl.card_id
        LEFT JOIN sets s ON s.id = c.set_id
        ORDER BY sl.id""").fetchall()
    with open(out / "sort_manifest.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Scan#", "Source", "Name", "No", "Variant", "Qty", "Sort into"])
        for i, (source, name, number, variant, qty, reason, canonical_set) in enumerate(rows, 1):
            bin_ = canonical_set if canonical_set else f"NEEDS REVIEW ({reason})"
            w.writerow([i, source, name, number, variant, qty, bin_])

if __name__ == "__main__":
    raise SystemExit("collection_core.py is a shared library, not a script -- "
                      "run sort_viewer.py, ingest_delver.py, or live_scanner.py instead.")
