import asyncio
import os

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

from core.bot import MusicBot

load_dotenv()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

async def init():
    await bot.add_cog(MusicBot(bot))
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

if __name__ == '__main__':
    asyncio.run(init())
    bot.run(os.getenv('DISCORD_API_KEY'))