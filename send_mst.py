import os
import requests

def msg_fun(message: str):
    """
    Sends a message to a Telegram bot.
    Requires an environment variable KEYS in the format: BOT_TOKEN_CHAT_ID
    Example: KEYS="123456789:ABCDEFghIJKLmnopQRSTUvwxYZ-987654321_123456789"
    """

    KEYS = os.getenv("KEYS")
    if not KEYS:
        raise ValueError("Environment variable 'KEYS' not found. Format: BOT_TOKEN_CHAT_ID")

    try:
        BOT_TOKEN, CHAT_ID = KEYS.split("_", 1)
    except ValueError:
        raise ValueError("Invalid KEYS format. Use BOT_TOKEN_CHAT_ID")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {
        "chat_id": CHAT_ID,
        "text": message
    }

    response = requests.get(url, params=params)
    data = response.json()

    if not data.get("ok"):
        print("❌ Failed to send message:", data)
    else:
        print("✅ Message sent successfully!")

    return data
