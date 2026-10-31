#!/usr/bin/env python3
"""
One-time migration: move the existing Pokemon-only collection.db schema
(cards/sets/collection/scan_log/imports/unmatched, built by the Delver CSV/
webhook pipeline) into the generic multi-TCG schema in collection_core.py,
tagging every row game_id='pokemon' and prefixing every id "pokemon:...".

Run once: ./venv/bin/python migrate_to_multigame.py
Safe to re-run: no-ops if old_* tables don't exist (already migrated).
"""
import shutil, sqlite3, sys
from pathlib import Path

import collection_core as core
import ingest_delver as ing

DB = "collection.db"

def table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

def main():
    if not Path(DB).exists():
        sys.exit(f"{DB} not found -- nothing to migrate.")
    db = sqlite3.connect(DB)

    if not table_exists(db, "cards") or table_exists(db, "games"):
        print("Nothing to migrate (no old-style `cards` table, or already migrated).")
        return

    # Detect old schema vs. already-generic schema: old `cards` has no game_id column.
    old_card_cols = {row[1] for row in db.execute("PRAGMA table_info(cards)")}
    if "game_id" in old_card_cols:
        print("`cards` already has game_id -- already migrated.")
        return

    backup = DB + ".bak"
    db.close()
    shutil.copy(DB, backup)
    print(f"Backed up {DB} -> {backup}")

    db = sqlite3.connect(DB)
    old_counts = {}
    for t in ["sets", "cards", "collection", "scan_log", "imports", "unmatched"]:
        old_counts[t] = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        db.execute(f"ALTER TABLE {t} RENAME TO old_{t}")
    db.commit()

    core.schema(db)
    db.execute("INSERT OR IGNORE INTO games VALUES (?,?)", ("pokemon", "Pokémon TCG"))

    db.execute("""INSERT INTO sets (id, game_id, name, series, release_date, printed_total, total)
        SELECT 'pokemon:' || id, 'pokemon', name, series, release_date, printed_total, total FROM old_sets""")

    db.execute("""INSERT INTO cards (id, game_id, set_id, name, number, rarity,
            tcg_market_normal, tcg_market_holo, tcg_market_reverse, price_updated, image_url)
        SELECT 'pokemon:' || id, 'pokemon', 'pokemon:' || set_id, name, number, rarity,
               tcg_market_normal, tcg_market_holo, tcg_market_reverse, price_updated, image_url
        FROM old_cards""")

    db.execute("""INSERT INTO collection (card_id, variant, condition, language, qty)
        SELECT 'pokemon:' || card_id, variant, condition, language, qty FROM old_collection""")

    # old_scan_log only ever stored the raw CSV text (no separate normalized
    # column existed yet) -- re-derive the normalized `variant` with Pokemon's
    # own norm_variant() so historical rows get a correct collection-bookkeeping key.
    old_scan_rows = db.execute("""SELECT id, card_id, raw_name, raw_number, raw_variant,
            qty, set_name, reason, condition, language, created FROM old_scan_log""").fetchall()
    for (sid, card_id, raw_name, raw_number, raw_variant, qty, set_name,
         reason, condition, language, created) in old_scan_rows:
        new_card_id = f"pokemon:{card_id}" if card_id else None
        variant = ing.norm_variant(raw_variant)
        db.execute("""INSERT INTO scan_log (id, game_id, source, card_id, raw_name, raw_number,
                raw_variant, variant, qty, set_name, reason, condition, language, created)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, "pokemon", "delver-csv", new_card_id, raw_name, raw_number, raw_variant,
             variant, qty, set_name, reason, condition, language, created))

    db.execute("INSERT INTO imports SELECT * FROM old_imports")
    db.execute("""INSERT INTO unmatched (game_id, raw_name, raw_set, raw_number, qty, reason)
        SELECT 'pokemon', raw_name, raw_set, raw_number, qty, reason FROM old_unmatched""")
    db.commit()

    print("Row counts (old -> new):")
    all_ok = True
    for t in ["sets", "cards", "collection", "scan_log", "imports", "unmatched"]:
        new_count = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        ok = new_count == old_counts[t]
        all_ok = all_ok and ok
        print(f"  {t}: {old_counts[t]} -> {new_count} {'OK' if ok else 'MISMATCH'}")

    if not all_ok:
        db.close()
        sys.exit(f"Row count mismatch -- NOT dropping old_* tables. Restore from {backup} if needed and investigate.")

    for t in ["sets", "cards", "collection", "scan_log", "imports", "unmatched"]:
        db.execute(f"DROP TABLE old_{t}")
    db.commit()
    print("Migration complete. old_* tables dropped.")

if __name__ == "__main__":
    main()
