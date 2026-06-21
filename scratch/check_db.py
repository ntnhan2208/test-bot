import sqlite3
import os

db_path = "/Users/ntn/Desktop/BOT/database.db"
if not os.path.exists(db_path):
    print("Database not found!")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

try:
    cursor.execute("SELECT id, marketplace, title, price, market_price, estimated_profit FROM seen_items ORDER BY id DESC LIMIT 50")
    rows = cursor.fetchall()
    
    print(f"{'ID':<4} | {'Marketplace':<18} | {'Price':<8} | {'Market Price':<12} | {'Title'}")
    print("-" * 100)
    for r in rows:
        mp = f"{r['market_price']:,}" if r['market_price'] is not None else "None"
        title_truncated = r['title'][:50] + "..." if len(r['title']) > 50 else r['title']
        print(f"{r['id']:<4} | {r['marketplace']:<18} | {r['price']:<8,} | {mp:<12} | {title_truncated}")
except Exception as e:
    print("Error:", e)
finally:
    conn.close()
