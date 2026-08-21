"""labeler.py — daily T+24h price labeling for paper decisions.

Cron: daily. For each BUY decision (or SKIP candidate) opened 24h ago,
fetch the current price once, compute correctness, store in `labels` table.
"""
import os
import sys
import time
import json
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from store import Store  # noqa: E402

UA = "OpenAI File Downloader, XaiImageApiFetch/1.0"

LABEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS labels (
    decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
    entry_price REAL NOT NULL,
    price_24h   REAL NOT NULL,
    correct     INTEGER NOT NULL,
    labeled_at  TEXT NOT NULL
);
"""

THRESHOLD = 0.02  # +2% beats fees+slippage on small positions


def fetch_price_dexscreener(chain, token):
    """One-shot price lookup via DexScreener token API."""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{token}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        pairs = data.get("pairs") or []
        if not pairs:
            return None
        best = max(pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0))
        return float(best["priceUsd"])
    except Exception:
        return None


def main():
    db = Store(os.path.join(os.path.dirname(__file__), "state", "dgr.db"))
    db.conn.executescript(LABEL_SCHEMA)

    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                           time.gmtime(time.time() - 86400 - 3600))
    until = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                          time.gmtime(time.time() - 86400 + 3600))

    # decisions made ~24h ago that don't have labels yet
    rows = db.query("""
        SELECT d.id, d.vote, c.token, c.chain, c.price_usd
        FROM decisions d JOIN candidates c ON c.id = d.candidate_id
        WHERE d.ts BETWEEN ? AND ?
          AND c.price_usd IS NOT NULL
          AND d.id NOT IN (SELECT decision_id FROM labels)
    """, (cutoff, until))

    print(f"Labeling {len(rows)} decisions from ~24h ago")
    for row in rows:
        price_now = fetch_price_dexscreener(row["chain"], row["token"])
        if price_now is None:
            print(f"  skip {row['id']} ({row['token']}): no price")
            continue
        entry = row["price_usd"]
        moved_up = (price_now / entry - 1) >= THRESHOLD if entry else False
        # BUY is correct when it went up; SKIP is correct when it didn't
        correct = (row["vote"] == "BUY") == moved_up
        db.conn.execute(
            "INSERT OR IGNORE INTO labels (decision_id, entry_price, price_24h, "
            "correct, labeled_at) VALUES (?, ?, ?, ?, ?)",
            (row["id"], entry, price_now, int(correct),
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        db.conn.commit()
        print(f"  #{row['id']} {row['vote']} {entry} -> {price_now}: "
              f"{'CORRECT' if correct else 'WRONG'}")

    # scoreboard
    total = db.query("SELECT COUNT(*) n, SUM(correct) c FROM labels")
    if total and total[0]["n"]:
        n, c = total[0]["n"], total[0]["c"] or 0
        print(f"\nRunning accuracy: {c}/{n} = {c/n*100:.0f}% "
              f"(target ≥55%)")


if __name__ == "__main__":
    main()
