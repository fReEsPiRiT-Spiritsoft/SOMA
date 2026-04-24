#!/usr/bin/env python3
"""Quick check of SOMA memory database."""
import sqlite3

db = sqlite3.connect("/home/patricks/Schreibtisch/SOMA/data/soma_memory.db")
cur = db.cursor()

# List tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("=== TABLES ===")
print(tables)

# Facts
if "facts" in tables:
    cur.execute("SELECT count(*) FROM facts")
    print(f"\n=== FACTS ({cur.fetchone()[0]} total) ===")
    cur.execute("SELECT id, category, subject, fact, confidence FROM facts ORDER BY rowid DESC LIMIT 20")
    for r in cur.fetchall():
        print(f"  [{r[1]}] {r[2]}: {r[3]} (conf={r[4]})")

# Search for Patrick specifically
if "facts" in tables:
    print("\n=== SEARCH: Patrick ===")
    cur.execute("SELECT * FROM facts WHERE fact LIKE '%Patrick%' OR subject LIKE '%Patrick%'")
    for r in cur.fetchall():
        print(f"  {r}")

# Episodes (last 10)
if "episodes" in tables:
    cur.execute("SELECT count(*) FROM episodes")
    print(f"\n=== EPISODES ({cur.fetchone()[0]} total, last 10) ===")
    cur.execute("SELECT id, summary, emotion, importance FROM episodes ORDER BY rowid DESC LIMIT 10")
    for r in cur.fetchall():
        s = (r[1] or "")[:120]
        print(f"  [{r[2]}] imp={r[3]}: {s}")

# Search episodes for Patrick
if "episodes" in tables:
    print("\n=== EPISODES with Patrick ===")
    cur.execute("SELECT id, summary, importance FROM episodes WHERE summary LIKE '%Patrick%' OR summary LIKE '%Entwickler%' OR summary LIKE '%geboren%' ORDER BY rowid DESC LIMIT 5")
    for r in cur.fetchall():
        s = (r[1] or "")[:150]
        print(f"  imp={r[2]}: {s}")

# Check semantic store if exists
for table in ["semantic_memories", "memories", "long_term"]:
    if table in tables:
        cur.execute(f"SELECT count(*) FROM {table}")
        print(f"\n=== {table} ({cur.fetchone()[0]} total) ===")
        cur.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 5")
        for r in cur.fetchall():
            print(f"  {str(r)[:200]}")

db.close()
