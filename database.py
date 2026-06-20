import sqlite3
import os
from datetime import datetime

DEFAULT_DB_PATH = os.environ.get("DATABASE_PATH")
if not DEFAULT_DB_PATH:
    if os.path.isdir("/app/data"):
        DEFAULT_DB_PATH = "/app/data/database.db"
    else:
        DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")

def init_db(db_path=DEFAULT_DB_PATH):
    """Initializes the database and creates the seen_items table if it doesn't exist."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS seen_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketplace TEXT NOT NULL,
            item_id TEXT NOT NULL,
            title TEXT,
            price INTEGER,
            url TEXT,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(marketplace, item_id)
        )
    """)
    # Create an index for faster lookups
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_seen_items_lookup 
        ON seen_items (marketplace, item_id)
    """)
    # Migrate database columns if necessary
    try:
        cursor.execute("ALTER TABLE seen_items ADD COLUMN market_price INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE seen_items ADD COLUMN estimated_profit INTEGER")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def is_item_seen(marketplace, item_id, db_path=DEFAULT_DB_PATH):
    """Checks if an item from a specific marketplace has already been seen."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM seen_items WHERE marketplace = ? AND item_id = ? LIMIT 1",
        (marketplace, str(item_id))
    )
    result = cursor.fetchone()
    conn.close()
    return result is not None

def mark_item_as_seen(marketplace, item_id, title, price, url, market_price=None, estimated_profit=None, db_path=DEFAULT_DB_PATH):
    """Marks an item as seen by inserting it into the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT OR IGNORE INTO seen_items (marketplace, item_id, title, price, url, market_price, estimated_profit, discovered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (marketplace, str(item_id), title, price, url, market_price, estimated_profit, datetime.utcnow())
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error marking item as seen: {e}")
    finally:
        conn.close()

