"""Register an already-sorted batch so future scans are checked against it.

    ./venv/bin/python add_prior_batch.py collection.db.batch-sorted-2026-10-11-1239 batch1

Copies that backup's `collection` rows into this db's `prior_collection` table
(re-running with the same batch name replaces it). sort_viewer's duplicate flag
then treats those cards as already owned. Clearing the live collection leaves
prior_collection alone.
"""
import sqlite3
import sys

src, batch = sys.argv[1], sys.argv[2]
db = sqlite3.connect("collection.db")
db.execute("""CREATE TABLE IF NOT EXISTS prior_collection (
    card_id TEXT, variant TEXT, condition TEXT, language TEXT,
    qty INTEGER NOT NULL DEFAULT 0, batch TEXT,
    PRIMARY KEY (card_id, variant, condition, language, batch))""")
db.execute("ATTACH DATABASE ? AS src", (src,))
db.execute("DELETE FROM prior_collection WHERE batch = ?", (batch,))
db.execute("""INSERT INTO prior_collection
    SELECT card_id, variant, condition, language, qty, ? FROM src.collection""", (batch,))
db.commit()
n, q = db.execute("SELECT COUNT(*), SUM(qty) FROM prior_collection WHERE batch=?", (batch,)).fetchone()
print(f"batch '{batch}': {n} distinct cards, {q} copies")
