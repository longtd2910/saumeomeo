import asyncio
import os
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

from core.bot import MusicBot
from core.database import PlaylistDatabase

VERSION = "1.0.8"
CHANGE_NOTE = "- Fix lỗi khi phát bài hát từ lịch sử"

load_dotenv()

discord.utils.setup_logging(level=logging.INFO, root=True)

logger = logging.getLogger(__name__)

def compare_versions(version1: str, version2: str) -> int:
    parts1 = [int(x) for x in version1.split('.')]
    parts2 = [int(x) for x in version2.split('.')]
    
    max_len = max(len(parts1), len(parts2))
    parts1.extend([0] * (max_len - len(parts1)))
    parts2.extend([0] * (max_len - len(parts2)))
    
    for p1, p2 in zip(parts1, parts2):
        if p1 > p2:
            return 1
        elif p1 < p2:
            return -1
    return 0

async def check_and_add_version():
    db = PlaylistDatabase()
    try:
        await db.connect()
        latest_version = await db.get_latest_version()
        
        if latest_version is None or compare_versions(VERSION, latest_version) > 0:
            success = await db.add_version(VERSION, CHANGE_NOTE)
            if success:
                logger.info(f"Added new version {VERSION} to database")
            else:
                logger.error(f"Failed to add version {VERSION} to database")
        else:
            logger.info(f"Version {VERSION} is not greater than latest version {latest_version}")
    except Exception as e:
        logger.error(f"Error checking/adding version: {e}")
    finally:
        await db.close()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)
bot.app_version = VERSION
bot.change_note = CHANGE_NOTE

async def init():
    await bot.add_cog(MusicBot(bot))

if __name__ == '__main__':
    asyncio.run(check_and_add_version())
    asyncio.run(init())
    bot.run(os.getenv('DISCORD_API_KEY'))