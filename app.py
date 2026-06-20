import os
import json
import threading
import asyncio
import logging
from flask import Flask, jsonify, request, render_template, send_from_directory
import sqlite3
import random
from datetime import datetime
from playwright.async_api import async_playwright

import database
import notifier
import scraper

# Configure logging to match main bot
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("JP_Bot.WebUI")

app = Flask(__name__, template_folder="templates")
CONFIG_PATH = "config.json"

class ScraperRunner:
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        self.status = "Stopped"  # "Stopped", "Running", "Scanning"
        self.last_run_time = None
        self.loop = None
        
    def start(self):
        if self.thread and self.thread.is_alive():
            return False
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_loop)
        self.thread.daemon = True
        self.thread.start()
        return True
        
    def stop(self):
        if self.thread and self.thread.is_alive():
            self.stop_event.set()
            self.status = "Stopped"
            return True
        return False

    def _run_loop(self):
        self.status = "Running"
        # Create a new event loop for this background thread
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._async_scraper_loop())
        except Exception as e:
            logger.error(f"Error in background scraper loop: {e}")
        finally:
            self.status = "Stopped"
            self.loop.close()
            self.loop = None
            
    async def _async_scraper_loop(self):
        logger.info("Background scraper thread started.")
        database.init_db()
        
        async with async_playwright() as p:
            logger.info("WebUI Bot: Launching headless browser...")
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
                extra_http_headers={"Accept-Language": "ja-JP,ja;q=0.9"}
            )
            page = await context.new_page()
            
            while not self.stop_event.is_set():
                self.status = "Scanning"
                self.last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Reload config every run cycle to capture UI updates
                try:
                    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                        config = json.load(f)
                except Exception as e:
                    logger.error(f"Failed to reload config: {e}")
                    self.status = "Running"
                    await asyncio.sleep(10)
                    continue

                searches = config.get("searches", [])
                interval_minutes = config.get("check_interval_minutes", 15)
                
                for idx, search_item in enumerate(searches):
                    if self.stop_event.is_set():
                        break
                        
                    keyword = search_item.get("keyword")
                    search_query = search_item.get("japanese_keyword") or keyword
                    min_price = search_item.get("min_price", 0)
                    max_price = search_item.get("max_price", 999999)
                    
                    logger.info(f"[UI Scraper] Scanning: '{search_query}' (Price: {min_price} - {max_price} JPY)")
                    
                    # 1. Yahoo Auctions
                    yahoo_items = await scraper.scrape_yahoo_auctions(page, search_query, min_price, max_price)
                    # Polite interruptible sleeps
                    for _ in range(random.randint(3, 7)):
                        if self.stop_event.is_set():
                            break
                        await asyncio.sleep(1)
                    
                    if self.stop_event.is_set():
                        break
                        
                    # 2. Yahoo! Fleamarket
                    fleamarket_items = await scraper.scrape_yahoo_fleamarket(page, search_query, min_price, max_price)
                    
                    all_found = yahoo_items + fleamarket_items
                    
                    # Parse excluded keywords for this search
                    exclude_str = search_item.get("exclude_keywords", "")
                    excludes = [k.strip().lower() for k in exclude_str.split(",") if k.strip()] if exclude_str else []
                    
                    new_items_count = 0
                    for item in all_found:
                        if self.stop_event.is_set():
                            break
                        marketplace = item["marketplace"]
                        item_id = item["item_id"]
                        title = item["title"]
                        price = item["price"]
                        url = item["url"]
                        
                        # Excluded keyword check (case-insensitive)
                        if excludes:
                            title_lower = title.lower()
                            if any(ex in title_lower for ex in excludes):
                                logger.info(f"[UI Scraper] Skipping '{title}' as it contains an excluded keyword.")
                                continue
                        
                        seen = database.is_item_seen(marketplace, item_id)
                        if not seen:
                            # Clean title and fetch market price specifically for this item
                            cleaned_title = scraper.clean_title_for_search(title)
                            logger.info(f"[UI Scraper] Fetching market price for new item '{title}' (query: '{cleaned_title}')")
                            item_market_price = await scraper.get_market_price(page, cleaned_title)
                            item["market_price"] = item_market_price if item_market_price > 0 else None
                            item["estimated_profit"] = (item_market_price - price) if item_market_price > 0 else None
                            
                            new_items_count += 1
                            profit_str = f" (Profit: ¥{item['estimated_profit']:,})" if item["estimated_profit"] is not None else ""
                            logger.info(f"[UI Scraper] [NEW] [{marketplace}] {title} - ¥{price:,}{profit_str}")
                            notifier.notify_all(config, item)
                            database.mark_item_as_seen(marketplace, item_id, title, price, url, item["market_price"], item["estimated_profit"])
                            
                            # Polite delay after querying comparison site
                            for _ in range(random.randint(2, 4)):
                                if self.stop_event.is_set():
                                    break
                                await asyncio.sleep(1)
                        else:
                            item["market_price"] = None
                            item["estimated_profit"] = None
                            
                    logger.info(f"[UI Scraper] Finished '{search_query}'. Discovered {new_items_count} new items.")
                    
                    # Wait between search keywords
                    if idx < len(searches) - 1:
                        for _ in range(random.randint(5, 10)):
                            if self.stop_event.is_set():
                                break
                            await asyncio.sleep(1)
                
                if self.stop_event.is_set():
                    break
                    
                self.status = "Running"
                logger.info(f"Cycle finished. Next run in {interval_minutes} minutes.")
                
                # Sleep between run cycles in small 1-sec chunks so we can interrupt immediately
                seconds_to_sleep = interval_minutes * 60
                for _ in range(int(seconds_to_sleep)):
                    if self.stop_event.is_set():
                        break
                    await asyncio.sleep(1)
            
            logger.info("Closing browser...")
            await browser.close()
            
        logger.info("Background scraper thread finished.")

# Global scraper runner instance
runner = ScraperRunner()

@app.route("/")
def index():
    """Serves the dashboard front-page."""
    return render_template("index.html")

@app.route("/api/status", methods=["GET"])
def get_status():
    """Returns the current bot status, stats, and search parameters."""
    conn = sqlite3.connect(database.DEFAULT_DB_PATH)
    cursor = conn.cursor()
    
    # Get total bargains count
    try:
        cursor.execute("SELECT COUNT(*) FROM seen_items")
        total_bargains = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        total_bargains = 0
    conn.close()
    
    # Load config to get current parameters
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {}
        
    return jsonify({
        "status": runner.status,
        "last_run": runner.last_run_time or "Never",
        "total_bargains": total_bargains,
        "active_searches": len(config.get("searches", [])),
        "check_interval_minutes": config.get("check_interval_minutes", 15)
    })

@app.route("/api/control", methods=["POST"])
def control_bot():
    """Starts or stops the scraper bot."""
    data = request.json or {}
    action = data.get("action")
    
    if action == "start":
        started = runner.start()
        return jsonify({"success": started, "status": runner.status})
    elif action == "stop":
        stopped = runner.stop()
        return jsonify({"success": stopped, "status": runner.status})
    else:
        return jsonify({"error": "Invalid action"}), 400

@app.route("/api/config", methods=["GET", "POST"])
def manage_config():
    """GET: returns config.json. POST: saves config.json."""
    if request.method == "GET":
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        return jsonify({"error": "Config not found"}), 404
        
    elif request.method == "POST":
        new_config = request.json
        if not new_config:
            return jsonify({"error": "Invalid config data"}), 400
            
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(new_config, f, indent=2, ensure_ascii=False)
            logger.info("Configuration updated via Web UI.")
            return jsonify({"success": True, "config": new_config})
        except Exception as e:
            return jsonify({"error": f"Failed to save config: {e}"}), 500

@app.route("/api/items", methods=["GET"])
def get_items():
    """Returns recent scraped items from the SQLite database."""
    limit = request.args.get("limit", default=50, type=int)
    search_query = request.args.get("search", default="", type=str)
    marketplace = request.args.get("marketplace", default="", type=str)
    
    conn = sqlite3.connect(database.DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        query = "SELECT * FROM seen_items WHERE 1=1"
        params = []
        
        if search_query:
            query += " AND (title LIKE ? OR item_id LIKE ?)"
            params.append(f"%{search_query}%")
            params.append(f"%{search_query}%")
            
        if marketplace:
            query += " AND marketplace = ?"
            params.append(marketplace)
            
        query += " ORDER BY discovered_at DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        items = []
        for row in rows:
            market_price = None
            estimated_profit = None
            try:
                market_price = row["market_price"]
                estimated_profit = row["estimated_profit"]
            except (IndexError, KeyError, sqlite3.OperationalError):
                pass
                
            items.append({
                "id": row["id"],
                "marketplace": row["marketplace"],
                "item_id": row["item_id"],
                "title": row["title"],
                "price": row["price"],
                "url": row["url"],
                "market_price": market_price,
                "estimated_profit": estimated_profit,
                "discovered_at": row["discovered_at"]
            })
        return jsonify(items)
    except sqlite3.OperationalError:
        return jsonify([])
    finally:
        conn.close()

@app.route("/api/logs", methods=["GET"])
def get_logs():
    """Reads the last N lines from the log file to stream to the UI."""
    limit = request.args.get("limit", default=100, type=int)
    log_file = "bot.log"
    if not os.path.exists(log_file):
        return jsonify([])
        
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return jsonify(lines[-limit:])
    except Exception as e:
        return jsonify([f"Error reading logs: {e}"])

@app.route("/api/clear-database", methods=["POST"])
def clear_database():
    """Deletes all items from the SQLite database."""
    try:
        conn = sqlite3.connect(database.DEFAULT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM seen_items")
        conn.commit()
        conn.close()
        logger.info("[UI Scraper] Database cleared by user.")
        return jsonify({"success": True, "message": "Database cleared successfully."})
    except Exception as e:
        logger.error(f"Error clearing database: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    # Ensure database table structure exists
    database.init_db()
    # Run server
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
