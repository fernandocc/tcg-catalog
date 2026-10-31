"""End-of-batch routine -- run once a batch is scanned AND sorted.

    ./venv/bin/python finish_batch.py

1. Backs up collection.db -> collection.db.batch<N>-<timestamp>
2. Registers the batch's cards in `prior_collection` as batch<N>, so every later
   batch's duplicate flags are checked against ALL earlier batches
3. Rewrites out/collection_current.csv (merged collection snapshot)
4. Clears the live scan tables (scan_log/collection/imports/unmatched), the viewer
   data and the generated out/ views, and the scanner's last-card memory

Refuses to run while live_scanner.py is running (it would keep adding cards).
"""
import shutil
import sqlite3
import time
from pathlib import Path

import appenv

appenv.enter_data_dir()
if appenv.pids_of("scanner"):
    raise SystemExit("live_scanner.py is still running -- stop it first.")

db = sqlite3.connect("collection.db")
db.execute("""CREATE TABLE IF NOT EXISTS prior_collection (
    card_id TEXT, variant TEXT, condition TEXT, language TEXT,
    qty INTEGER NOT NULL DEFAULT 0, batch TEXT,
    PRIMARY KEY (card_id, variant, condition, language, batch))""")
n_scans = db.execute("SELECT COUNT(*) FROM scan_log").fetchone()[0]
if n_scans == 0:
    raise SystemExit("Nothing to finish: scan_log is empty.")

nums = [int(b[5:]) for (b,) in db.execute("SELECT DISTINCT batch FROM prior_collection") if b.startswith("batch") and b[5:].isdigit()]
batch = f"batch{max(nums, default=0) + 1}"
backup = f"collection.db.{batch}-{time.strftime('%Y%m%d-%H%M')}"
shutil.copy("collection.db", backup)

db.execute("""INSERT INTO prior_collection
    SELECT card_id, variant, condition, language, qty, ? FROM collection""", (batch,))
for t in ("scan_log", "collection", "imports", "unmatched"):
    db.execute(f"DELETE FROM {t}")
db.commit()

Path("viewer_data.json").write_text("[]")
Path("live_last_committed.json").unlink(missing_ok=True)
for f in list(Path("out").glob("*.csv")) + list(Path("out/sets").glob("*.csv")):
    f.unlink()

db.close()
from sort_viewer import write_collection_snapshot
write_collection_snapshot()
db = sqlite3.connect("collection.db")
total = db.execute("SELECT COUNT(*), SUM(qty) FROM prior_collection").fetchone()
print(f"{batch}: {n_scans} scans backed up to {backup}")
print(f"prior_collection now holds {total[0]} distinct cards / {total[1]} copies across all finished batches")
