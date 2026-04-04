import asyncio
import logging
import os

from curl_cffi import requests

from .reply_format import markdown_to_discord

logger = logging.getLogger(__name__)

CHAT_URL = "https://saudanhbac.pleiadex.dev/chat"


def _post_chat_sync(content: str, hard_key: str):
    r = requests.post(
        CHAT_URL,
        json={"content": content},
        headers={
            "Authorization": f"Bearer {hard_key}",
            "Content-Type": "application/json",
        },
        timeout=300,
    )
    try:
        body = r.json()
    except Exception:
        return None, f"Invalid response (HTTP {r.status_code})"
    code = body.get("code")
    if code == 200:
        data = body.get("data")
        if isinstance(data, str):
            return data, None
        return None, "Empty reply from assistant"
    msg = body.get("message") or f"Error (code {code})"
    return None, msg


async def analyst_chat_reply(content: str) -> str:
    key = os.getenv("HARD_KEY")
    if not key:
        logger.error("HARD_KEY is not set")
        return "Bot is not configured (HARD_KEY missing)."
    text = content.strip()
    if not text:
        return "Say something."
    reply, err = await asyncio.to_thread(_post_chat_sync, text, key)
    if err is not None:
        logger.warning("Analyst chat failed: %s", err)
        return err
    return markdown_to_discord(reply)
