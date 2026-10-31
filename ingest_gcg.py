#!/usr/bin/env python3
"""
STUB — not implemented. Tabled until the Pokémon ingest path is proven
and the scan source for Gundam cards is confirmed (unknown whether
Poke Delver / Card Slinger recognize this game).

Plan: mirror ingest_delver.py's shape once a scan source exists.
  - Data source: https://gcgapi.com/ (free, unofficial, weekly JSON/NDJSON
    dump — prefer downloading that dump over hitting the live API, since
    it needs no key and has no rate limit).
  - DB: separate file (e.g. collection_gcg.db) or a `game` column added
    to the shared schema — decide when building this out.
"""
DUMP_URL = "https://gcgapi.com"  # TODO: confirm exact JSON/NDJSON dump URL from docs before use

if __name__ == "__main__":
    raise SystemExit("ingest_gcg.py is a stub — not implemented yet")
