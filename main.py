import os
import sys
import json
import argparse
import asyncio
import logging
import random
from datetime import datetime
from playwright.async_api import async_playwright

import database
import notifier
import scraper

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("JP_Bot.Main")

# Load configuration
def load_config(config_path):
    if not os.path.exists(config_path):
        logger.error(f"Configuration file not found at {config_path}")
        sys.exit(1)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to parse config file: {e}")
        sys.exit(1)

BROWSER_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-gpu",
    "--no-first-run",
    "--no-zygote",
    "--single-process",
    "--disable-extensions",
    "--disable-component-extensions",
    "--js-flags=--max-old-space-size=128"
]

CONTEXT_OPTIONS = {
    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "viewport": {"width": 1280, "height": 800},
    "locale": "ja-JP",
    "timezone_id": "Asia/Tokyo",
    "extra_http_headers": {"Accept-Language": "ja-JP,ja;q=0.9"}
}

async def block_resources(route):
    """Block images, CSS, fonts and media to save RAM."""
    if route.request.resource_type in ["image", "stylesheet", "font", "media"]:
        await route.abort()
    else:
        await route.continue_()

async def create_page(browser):
    """Create a new browser context and page with resource blocking."""
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()
    await page.route("**/*", block_resources)
    return context, page

async def safe_get_market_price(browser, cleaned_title):
    """Fetch market price using an isolated, disposable browser context.
    This prevents a Mercari page crash from killing the main scraping page."""
    context = None
    try:
        context, page = await create_page(browser)
        result = await scraper.get_market_price(page, cleaned_title)
        return result
    except Exception as e:
        logger.warning(f"Market price lookup failed for '{cleaned_title}': {e}")
        return 0
    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass

async def run_search_cycle(config, browser, dry_run=False):
    """Executes a single check cycle for all configured searches."""
    logger.info("Starting check cycle...")
    
    searches = config.get("searches", [])
    if not searches:
        logger.warning("No searches configured in config.json.")
        return
    
    # Create a fresh context+page for scraping Yahoo/PayPay each cycle
    scrape_context, scrape_page = await create_page(browser)
    
    try:
        for idx, search_item in enumerate(searches):
            keyword = search_item.get("keyword")
            # Use Japanese keyword if defined, fallback to English keyword
            search_query = search_item.get("japanese_keyword") or keyword
            min_price = search_item.get("min_price", 0)
            max_price = search_item.get("max_price", 999999)
            
            logger.info(f"Processing search {idx+1}/{len(searches)}: '{search_query}' (Price: {min_price} - {max_price} JPY)")
            
            # 1. Scrape Yahoo Auctions
            yahoo_items = await scraper.scrape_yahoo_auctions(scrape_page, search_query, min_price, max_price)
            await asyncio.sleep(random.uniform(3, 7)) # polite delay
            
            # 2. Scrape Yahoo! Fleamarket
            fleamarket_items = await scraper.scrape_yahoo_fleamarket(scrape_page, search_query, min_price, max_price)
            
            all_found = yahoo_items + fleamarket_items
            logger.info(f"Cycle completed for '{search_query}'. Found {len(all_found)} total items across all marketplaces.")
            
            # Parse excluded keywords for this search
            exclude_str = search_item.get("exclude_keywords", "")
            excludes = [k.strip().lower() for k in exclude_str.split(",") if k.strip()] if exclude_str else []
            if excludes:
                logger.info(f"Excluding items containing: {excludes}")
            
            new_items_count = 0
            for item in all_found:
                marketplace = item["marketplace"]
                item_id = item["item_id"]
                title = item["title"]
                price = item["price"]
                url = item["url"]
                
                # Excluded keyword check (case-insensitive)
                if excludes:
                    title_lower = title.lower()
                    if any(ex in title_lower for ex in excludes):
                        logger.info(f"Skipping item '{title}' as it contains an excluded keyword.")
                        continue
                
                # Check database for deduplication
                seen = database.is_item_seen(marketplace, item_id)
                if not seen:
                    # Clean title and fetch market price in an ISOLATED context
                    cleaned_title = scraper.clean_title_for_search(title)
                    logger.info(f"Fetching market price for new item '{title}' (query: '{cleaned_title}')")
                    item_market_price = await safe_get_market_price(browser, cleaned_title)
                    item["market_price"] = item_market_price if item_market_price > 0 else None
                    item["estimated_profit"] = (item_market_price - price) if item_market_price > 0 else None
                    
                    new_items_count += 1
                    profit_str = f" - Profit: JPY {item['estimated_profit']:,}" if item["estimated_profit"] is not None else ""
                    logger.info(f"[NEW ITEM] [{marketplace}] {title} - JPY {price:,}{profit_str} - {url}")
                    
                    if not dry_run:
                        # Notify
                        notifier.notify_all(config, item)
                        # Mark as seen in SQLite database
                        database.mark_item_as_seen(marketplace, item_id, title, price, url, item["market_price"], item["estimated_profit"])
                    else:
                        logger.info(f"[DRY-RUN] Would have notified and saved: {title}")
                    
                    # Polite delay after querying comparison site
                    await asyncio.sleep(random.uniform(2, 4))
                else:
                    item["market_price"] = None
                    item["estimated_profit"] = None
            
            logger.info(f"Finished search for '{search_query}'. Discovered {new_items_count} new items.")
            
            # polite delay between search items
            if idx < len(searches) - 1:
                await asyncio.sleep(random.uniform(5, 10))
    finally:
        # Always close the scraping context at the end of the cycle
        try:
            await scrape_context.close()
        except Exception:
            pass

    logger.info("Check cycle completed.")

async def main():
    parser = argparse.ArgumentParser(description="Japanese Marketplace Price-Alert Bot")
    parser.add_argument("--config", default="config.json", help="Path to config.json file")
    parser.add_argument("--dry-run", action="store_true", help="Scrape and print items without writing to DB or sending notifications")
    parser.add_argument("--once", action="store_true", help="Run once and exit immediately")
    args = parser.parse_args()
    
    logger.info("Initializing JP Marketplace Alert Bot...")
    
    # 1. Load config
    config = load_config(args.config)
    
    # 2. Init database
    database.init_db()
    logger.info("Database initialized.")
    if args.dry_run:
        logger.info("Running in DRY-RUN mode. Notifications and database inserts are bypassed.")
        
    # 3. Setup check interval
    interval_minutes = config.get("check_interval_minutes", 15)
    
    # 4. Start Playwright and run loop
    async with async_playwright() as p:
        logger.info("Launching headless browser...")
        browser = await p.chromium.launch(
            headless=True,
            args=BROWSER_ARGS
        )
        
        try:
            if args.once:
                await run_search_cycle(config, browser, args.dry_run)
            else:
                while True:
                    await run_search_cycle(config, browser, args.dry_run)
                    logger.info(f"Sleeping for {interval_minutes} minutes before next check...")
                    await asyncio.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            logger.info("Bot manually stopped by KeyboardInterrupt.")
        except Exception as e:
            logger.exception(f"Unexpected error in main loop: {e}")
        finally:
            logger.info("Closing browser...")
            await browser.close()
            logger.info("Bot execution finished.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped.")
        sys.exit(0)

