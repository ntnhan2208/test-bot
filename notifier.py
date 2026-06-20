import requests
import json
import logging

logger = logging.getLogger("JP_Bot.Notifier")

def format_price(price):
    """Formats an integer price to JPY currency string (e.g., ¥12,345)."""
    if isinstance(price, str) and price.startswith("¥"):
        return price
    try:
        return f"¥{int(price):,}"
    except (ValueError, TypeError):
        return f"¥{price}"

def send_discord_webhook(webhook_url, item):
    """Sends a rich embed notification to Discord."""
    if not webhook_url or "YOUR_DISCORD" in webhook_url:
        return

    # Color mapping for marketplaces
    color_map = {
        "Mercari": 16729219,         # Reddish/Coral (#FF3B30)
        "Rakuma": 18274,             # Crimson/Red (#004762 or Rakuten Red)
        "Yahoo Auctions": 16768256,   # Yellow (#FFCC00)
        "Yahoo! Fleamarket": 14882920 # Pinkish/Red (#e31868)
    }
    color = color_map.get(item["marketplace"], 3447003) # Default blue

    fields = [
        {"name": "Price", "value": format_price(item["price"]), "inline": True},
        {"name": "Marketplace", "value": item["marketplace"], "inline": True}
    ]
    if item.get("market_price"):
        fields.append({"name": "Market Price", "value": format_price(item["market_price"]), "inline": True})
    if item.get("estimated_profit"):
        val = item["estimated_profit"]
        profit_str = val if isinstance(val, str) else f"+{format_price(val)}"
        fields.append({"name": "Estimated Profit", "value": profit_str, "inline": True})

    embed = {
        "title": item["title"],
        "url": item["url"],
        "color": color,
        "fields": fields,
        "footer": {"text": "JP Marketplace Alert Bot"}
    }

    if item.get("image_url"):
        embed["thumbnail"] = {"url": item["image_url"]}

    payload = {"embeds": [embed]}

    try:
        response = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        if response.status_code not in (200, 204):
            logger.error(f"Failed to send Discord webhook: {response.status_code} - {response.text}")
        else:
            logger.info(f"Successfully sent Discord notification for: {item['title']}")
    except Exception as e:
        logger.error(f"Error sending Discord webhook: {e}")

def send_telegram_message(bot_token, chat_id, item):
    """Sends a photo with a formatted caption or text message to Telegram."""
    if not bot_token or not chat_id or "YOUR_TELEGRAM" in bot_token:
        return

    # Format the caption text using HTML
    caption = (
        f"<b>🚨 NEW BARGAIN FOUND!</b>\n\n"
        f"<b>Title:</b> {item['title']}\n"
        f"<b>Price:</b> {format_price(item['price'])}\n"
    )
    if item.get("market_price"):
        caption += f"<b>Market Price:</b> {format_price(item['market_price'])}\n"
    if item.get("estimated_profit"):
        val = item["estimated_profit"]
        profit_str = val if isinstance(val, str) else f"+{format_price(val)}"
        caption += f"<b>Est. Profit:</b> {profit_str}\n"
    caption += (
        f"<b>Marketplace:</b> {item['marketplace']}\n\n"
        f"🔗 <a href='{item['url']}'>View Listing on {item['marketplace']}</a>"
    )

    image_url = item.get("image_url")
    try:
        if image_url:
            # Try sending as photo
            url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            payload = {
                "chat_id": chat_id,
                "photo": image_url,
                "caption": caption,
                "parse_mode": "HTML"
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info(f"Successfully sent Telegram photo notification for: {item['title']}")
                return
            else:
                logger.warning(f"Failed to send Telegram photo, falling back to text: {response.text}")

        # Fallback to normal text message
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Failed to send Telegram message: {response.status_code} - {response.text}")
        else:
            logger.info(f"Successfully sent Telegram text notification for: {item['title']}")
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")

def send_line_message(channel_access_token, user_id, item):
    """Sends a push message (text + optional image) to LINE via the Messaging API."""
    if not channel_access_token or not user_id or "YOUR_LINE" in channel_access_token:
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {channel_access_token}"
    }

    text = (
        f"🚨 NEW BARGAIN FOUND!\n\n"
        f"Title: {item['title']}\n"
        f"Price: {format_price(item['price'])}\n"
    )
    if item.get("market_price"):
        text += f"Market Price: {format_price(item['market_price'])}\n"
    if item.get("estimated_profit"):
        val = item["estimated_profit"]
        profit_str = val if isinstance(val, str) else f"+{format_price(val)}"
        text += f"Est. Profit: {profit_str}\n"
    text += (
        f"Marketplace: {item['marketplace']}\n\n"
        f"Link: {item['url']}"
    )

    messages = [
        {
            "type": "text",
            "text": text
        }
    ]

    # If there is an image, attach it as an image message
    if item.get("image_url"):
        messages.append({
            "type": "image",
            "originalContentUrl": item["image_url"],
            "previewImageUrl": item["image_url"]
        })

    payload = {
        "to": user_id,
        "messages": messages
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"Failed to send LINE message: {response.status_code} - {response.text}")
        else:
            logger.info(f"Successfully sent LINE notification for: {item['title']}")
    except Exception as e:
        logger.error(f"Error sending LINE message: {e}")

def notify_all(config, item):
    """Orchestrates sending notifications to all enabled channels."""
    notif_config = config.get("notifications", {})
    
    # Discord
    discord_url = notif_config.get("discord_webhook_url")
    if discord_url and discord_url.strip() and "YOUR_DISCORD" not in discord_url:
        send_discord_webhook(discord_url, item)
        
    # Telegram
    tg_config = notif_config.get("telegram", {})
    tg_token = tg_config.get("bot_token")
    tg_chat_id = tg_config.get("chat_id")
    if tg_token and tg_chat_id and "YOUR_TELEGRAM" not in tg_token:
        send_telegram_message(tg_token, tg_chat_id, item)

    # LINE
    line_config = notif_config.get("line", {})
    line_token = line_config.get("channel_access_token")
    line_user_id = line_config.get("user_id")
    if line_token and line_user_id and "YOUR_LINE" not in line_token:
        send_line_message(line_token, line_user_id, item)
