import urllib.parse
import re
import logging
from playwright.async_api import Page

logger = logging.getLogger("JP_Bot.Scraper")

def clean_price(price_str):
    """Cleans price string and converts to integer JPY."""
    if not price_str:
        return 0
    # Remove currency symbols, commas, and "円"
    cleaned = re.sub(r"[^\d]", "", price_str)
    try:
        return int(cleaned)
    except ValueError:
        return 0

def clean_title_for_search(title):
    """Cleans a Japanese listing title to create an effective search query for comparison sites."""
    if not title:
        return ""
    # Remove ellipsis/dots from truncation
    title = title.replace("...", " ").replace("..", " ")
    
    # 1. Remove text inside brackets
    title = re.sub(r'【[^】]*】', ' ', title)
    title = re.sub(r'\[[^\]]*\]', ' ', title)
    title = re.sub(r'\([^\)]*\)', ' ', title)
    title = re.sub(r'（[^）]*）', ' ', title)
    
    # 2. Remove common commercial filler terms in Japanese listings
    fillers = [
        "新品", "未使用", "美品", "超美品", "ジャンク", "ジャンク品", "訳あり", 
        "送料無料", "即購入OK", "即購入可", "中古", "動作確認済", "動作品", 
        "まとめ売り", "本体", "国内発送", "説明欄必読", "動作正常", "動作確認済み",
        "動作OK", "良品"
    ]
    for filler in fillers:
        title = title.replace(filler, " ")
        
    # 3. Clean up punctuation and symbols (keeping hyphens and slashes for model names)
    title = re.sub(r'[★☆◆◇■□●○▲▼！!？?＆&＋+|♪※]', ' ', title)
    
    # 4. Remove seller/warehouse codes (e.g. B26-893, J698225Y, A1708)
    title = re.sub(r'\b[A-Z]?\d{2,}-\d{2,}\b', ' ', title)  # B26-893 pattern
    title = re.sub(r'\b[A-Z]\d{5,}\w*\b', ' ', title)  # J698225Y pattern
    
    # 5. Remove Apple product codes (e.g. MGND3J/A, MVVK2J/A, MK183J/A)
    title = re.sub(r'\b[A-Z]{2,}\d+[A-Z]*/[A-Z]\b', ' ', title)
    
    # 6. Remove the word "Apple" - rarely used in Mercari JP listings and pollutes search
    title = re.sub(r'\bApple\b', ' ', title, flags=re.IGNORECASE)
    
    title = " ".join(title.split())
    
    # 7. Limit to the first 4 words to keep search queries short and effective
    words = title.split()
    if len(words) > 4:
        title = " ".join(words[:4])
        
    return title

async def scrape_yahoo_auctions(page: Page, keyword: str, min_price: int, max_price: int):
    """Scrapes Yahoo Auctions JP for matching items (newest first)."""
    items = []
    try:
        keyword_encoded = urllib.parse.quote(keyword)
        url = f"https://auctions.yahoo.co.jp/search/search?p={keyword_encoded}&aucminprice={min_price}&aucmaxprice={max_price}&s1=new&o1=d"
        logger.info(f"Scraping Yahoo Auctions: {url}")
        
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        # Give JS a small window to run
        await page.wait_for_timeout(2000)
        
        product_elements = await page.locator("li.Product").all()
        logger.info(f"Yahoo Auctions: Found {len(product_elements)} raw elements.")
        
        for element in product_elements:
            try:
                # Title & URL
                title_el = element.locator(".Product__titleLink").first
                if await title_el.count() == 0:
                    continue
                title = (await title_el.inner_text()).strip()
                href = await title_el.get_attribute("href")
                if not href:
                    continue
                
                # Extract clean item ID from URL (e.g. from /auction/p12345)
                # Typical URLs: https://auctions.yahoo.co.jp/jp/auction/g112003
                item_id = href.split("/")[-1]
                
                # Price
                price_el = element.locator(".Product__priceValue").first
                price_text = await price_el.inner_text() if await price_el.count() > 0 else "0"
                price = clean_price(price_text)
                
                # Image
                img_el = element.locator(".Product__imageData").first
                if await img_el.count() > 0:
                    image_url = await img_el.get_attribute("src")
                else:
                    img_el = element.locator("img").first
                    image_url = await img_el.get_attribute("src") if await img_el.count() > 0 else None
                
                # Validation
                if price == 0 or not title:
                    continue
                
                items.append({
                    "marketplace": "Yahoo Auctions",
                    "item_id": item_id,
                    "title": title,
                    "price": price,
                    "url": href,
                    "image_url": image_url
                })
            except Exception as item_err:
                logger.debug(f"Failed to parse Yahoo Auctions item: {item_err}")
                continue
                
    except Exception as e:
        logger.error(f"Error scraping Yahoo Auctions: {e}")
        
    return items

async def scrape_mercari(page: Page, keyword: str, min_price: int, max_price: int):
    """Scrapes Mercari JP for matching items (newest first)."""
    items = []
    try:
        keyword_encoded = urllib.parse.quote(keyword)
        # sort_order=created_time ensures newest items first
        url = f"https://jp.mercari.com/search?keyword={keyword_encoded}&price_min={min_price}&price_max={max_price}&status=on_sale&sort_order=created_time"
        logger.info(f"Scraping Mercari: {url}")
        
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        # Mercari relies heavily on JS framework hydration
        await page.wait_for_timeout(5000)
        
        grid_items = await page.locator("li[data-testid='item-cell']").all()
        if not grid_items:
            # Fallback selector
            grid_items = await page.locator("mer-item-thumbnail").all()
            
        logger.info(f"Mercari: Found {len(grid_items)} raw elements.")
        
        for item in grid_items:
            try:
                # Link
                link_el = item.locator("a").first
                if await link_el.count() == 0:
                    continue
                href = await link_el.get_attribute("href")
                if not href:
                    continue
                full_url = urllib.parse.urljoin("https://jp.mercari.com", href)
                
                # Extract item ID from URL (e.g. m12345678)
                item_id = href.split("/")[-1]
                
                # Image
                img_el = item.locator("img").first
                img_url = await img_el.get_attribute("src") if await img_el.count() > 0 else None
                
                # Title - clean up "のサムネイル" suffix which Mercari alt text has
                title_alt = await img_el.get_attribute("alt") if await img_el.count() > 0 else ""
                title = title_alt.replace("のサムネイル", "").strip() if title_alt else "Mercari Item"
                
                # Price extraction:
                # 1. Try thumbnail container's aria-label which contains JPY price even when geolocated to VND
                price = None
                thumbnail_el = item.locator("[id^='m']").first
                if await thumbnail_el.count() > 0:
                    label = await thumbnail_el.get_attribute("aria-label")
                    if label:
                        jpy_match = re.search(r"([\d,]+)円", label)
                        if jpy_match:
                            price = clean_price(jpy_match.group(1))
                
                # 2. Fallback to parsing from visible price text
                if price is None or price == 0:
                    price_el = item.locator("[class*='price']").first
                    price_text = await price_el.inner_text() if await price_el.count() > 0 else "0"
                    price = clean_price(price_text)
                
                if not price or not item_id:
                    continue
                    
                items.append({
                    "marketplace": "Mercari",
                    "item_id": item_id,
                    "title": title,
                    "price": price,
                    "url": full_url,
                    "image_url": img_url
                })
            except Exception as item_err:
                logger.debug(f"Failed to parse Mercari item: {item_err}")
                continue
                
    except Exception as e:
        logger.error(f"Error scraping Mercari: {e}")
        
    return items

async def scrape_rakuma(page: Page, keyword: str, min_price: int, max_price: int):
    """Scrapes Rakuma (Fril.jp) for matching items (newest first)."""
    items = []
    try:
        keyword_encoded = urllib.parse.quote(keyword)
        # sort=created_at&order=desc sorts by newest
        url = f"https://fril.jp/s?query={keyword_encoded}&transaction=selling&sort=created_at&order=desc"
        logger.info(f"Scraping Rakuma: {url}")
        
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(3000)
        
        product_elements = await page.locator(".item-box").all()
        logger.info(f"Rakuma: Found {len(product_elements)} raw elements.")
        
        for element in product_elements:
            try:
                # Link
                link_el = element.locator("a.item-box__image-link").first
                if await link_el.count() == 0:
                    link_el = element.locator("a").first
                href = await link_el.get_attribute("href") if await link_el.count() > 0 else None
                if not href:
                    continue
                
                # Extract item ID from URL (e.g. from https://item.fril.jp/itemid)
                item_id = href.split("/")[-1]
                
                # Title
                title_el = element.locator(".item-box__item-name").first
                if await title_el.count() == 0:
                    title_el = element.locator(".item-name").first
                title = (await title_el.inner_text()).strip() if await title_el.count() > 0 else "Rakuma Item"
                
                # Price
                price_el = element.locator(".item-box__item-price").first
                if await price_el.count() == 0:
                    price_el = element.locator(".item-price").first
                price_text = await price_el.inner_text() if await price_el.count() > 0 else "0"
                price = clean_price(price_text)
                
                # Local Price Filter (since Rakuma search doesn't easily parameterize price bounds in URL)
                if min_price > 0 and price < min_price:
                    continue
                if max_price > 0 and price > max_price:
                    continue
                
                # Image
                img_el = element.locator("img").first
                image_url = await img_el.get_attribute("src") if await img_el.count() > 0 else None
                
                items.append({
                    "marketplace": "Rakuma",
                    "item_id": item_id,
                    "title": title,
                    "price": price,
                    "url": href,
                    "image_url": image_url
                })
            except Exception as item_err:
                logger.debug(f"Failed to parse Rakuma item: {item_err}")
                continue
                
    except Exception as e:
        logger.error(f"Error scraping Rakuma: {e}")
        
    return items

async def scrape_yahoo_fleamarket(page: Page, keyword: str, min_price: int, max_price: int):
    """Scrapes Yahoo! Fleamarket (PayPay Fleamarket) for matching items (newest first)."""
    items = []
    try:
        keyword_encoded = urllib.parse.quote(keyword)
        # sort=openTime&order=desc sorts by newest
        url = f"https://paypayfleamarket.yahoo.co.jp/search/{keyword_encoded}?sort=openTime&order=desc"
        logger.info(f"Scraping Yahoo! Fleamarket: {url}")
        
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        # Wait a bit for JS execution
        await page.wait_for_timeout(3000)
        
        # Locate all a tags with href containing /item/
        links = await page.locator("a").all()
        logger.info(f"Yahoo! Fleamarket: Found {len(links)} raw links.")
        
        for l in links:
            try:
                href = await l.get_attribute("href")
                if not href or "/item/" not in href:
                    continue
                
                # Full URL
                full_url = urllib.parse.urljoin("https://paypayfleamarket.yahoo.co.jp", href)
                item_id = href.split("/")[-1]
                
                # Image
                img_el = l.locator("img").first
                title = ""
                image_url = ""
                if await img_el.count() > 0:
                    title = await img_el.get_attribute("alt")
                    image_url = await img_el.get_attribute("src")
                
                if not title:
                    title = (await l.inner_text()).strip()
                    
                # Find price from inner text (ends with "円")
                inner_text = await l.inner_text()
                price_match = re.search(r"([\d,]+)円", inner_text)
                price = 0
                if price_match:
                    price = clean_price(price_match.group(1))
                
                # Validation
                if price == 0 or not title or not item_id:
                    continue
                    
                # Local Price Filter
                if min_price > 0 and price < min_price:
                    continue
                if max_price > 0 and price > max_price:
                    continue
                    
                items.append({
                    "marketplace": "Yahoo! Fleamarket",
                    "item_id": item_id,
                    "title": title,
                    "price": price,
                    "url": full_url,
                    "image_url": image_url
                })
            except Exception as item_err:
                logger.debug(f"Failed to parse PayPay Fleamarket item: {item_err}")
                continue
    except Exception as e:
        logger.error(f"Error scraping Yahoo! Fleamarket: {e}")
        
    return items

async def get_market_price(page: Page, keyword: str):
    """Calculates the average market price of the 15 lowest-priced successfully sold items from Mercari."""
    
    keyword_encoded = urllib.parse.quote(keyword)
    url_mercari = f"https://jp.mercari.com/search?keyword={keyword_encoded}&status=trading_sold&sort_order=created_time"
    
    # Try up to 2 attempts (retry once on failure)
    for attempt in range(2):
        prices = []
        try:
            logger.info(f"Querying Mercari for sold market price (attempt {attempt+1}): {url_mercari}")
            
            await page.goto(url_mercari, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(4000)
            
            grid_items = await page.locator("li[data-testid='item-cell']").all()
            if not grid_items:
                grid_items = await page.locator("mer-item-thumbnail").all()
                
            for item in grid_items[:40]:  # sample up to 40 items
                try:
                    price = None
                    thumbnail_el = item.locator("[id^='m']").first
                    if await thumbnail_el.count() > 0:
                        label = await thumbnail_el.get_attribute("aria-label")
                        if label:
                            jpy_match = re.search(r"([\d,]+)円", label)
                            if jpy_match:
                                price = clean_price(jpy_match.group(1))
                    
                    if price is None or price == 0:
                        price_el = item.locator("[class*='price']").first
                        if await price_el.count() > 0:
                            price_text = await price_el.inner_text()
                            if any(marker in price_text for marker in ["VND", "₫", "đ"]):
                                continue
                            price = clean_price(price_text)
                        
                    if price and price > 0:
                        prices.append(price)
                except Exception:
                    continue
                    
            logger.info(f"Mercari comparison: extracted {len(prices)} JPY prices (attempt {attempt+1}).")
            
            if prices:
                # Sort ascending (cheapest first)
                sorted_prices = sorted(prices)
                lowest_15 = sorted_prices[:15]
                avg_price = int(sum(lowest_15) / len(lowest_15))
                logger.info(f"Calculated average of 15 lowest JPY prices for '{keyword}': JPY {avg_price:,} (from sample size of {len(prices)})")
                return avg_price
            
            # If no prices found on first attempt, wait and retry
            if attempt == 0:
                logger.info(f"No prices found for '{keyword}', retrying in 3 seconds...")
                await page.wait_for_timeout(3000)
                
        except Exception as e:
            logger.warning(f"Failed to scrape Mercari for market price (attempt {attempt+1}): {e}")
            if attempt == 0:
                await page.wait_for_timeout(3000)
        
    logger.warning(f"Could not extract any prices for '{keyword}' market calculation after 2 attempts.")
    return 0

