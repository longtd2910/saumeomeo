import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from core.bot import MusicBot

load_dotenv()

bot = commands.Bot(command_prefix='!', intents=discord.Intents.all())

async def init():
    await bot.add_cog(MusicBot(bot))

if __name__ == '__main__':
    asyncio.run(init())
    bot.run(os.getenv('API_KEY'))