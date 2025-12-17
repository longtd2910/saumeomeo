from dataclasses import dataclass
import discord
import asyncio
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime

from core.state import global_state
from core.database import PlaylistDatabase
from core.controller import play_logic, resolve_link_for_guild
from core.view import construct_queue_menu

@dataclass
class Context:
    interaction: discord.Interaction

def get_music_bot_cog(interaction: discord.Interaction):
    bot = interaction.client
    cog = bot.get_cog('MusicBot')
    return cog

async def _play_async(interaction: discord.Interaction, query: str) -> str:
    cog = get_music_bot_cog(interaction)
    if not cog:
        return "Error: MusicBot cog not found"
    
    state = global_state
    db = cog.db
    
    async def resolve_link_func(voice_id, link):
        return await resolve_link_for_guild(voice_id, link, cog.bot.loop, state)
    
    async def construct_queue_menu_func(interaction):
        guild = interaction.guild
        if not guild:
            embed = discord.Embed(title="📃   Danh sách chờ   📃")
            return None, embed
        
        voice_client = guild.voice_client
        guild_id = guild.id
        
        from core.view import construct_queue_menu
        return construct_queue_menu(
            state,
            voice_client,
            guild_id,
            cog._pause_logic,
            cog._resume_logic,
            cog._skip_logic,
            interaction
        )
    
    async def play_next_func(interaction):
        await cog.play_next(interaction)
    
    await play_logic(
        interaction,
        query,
        state,
        db,
        resolve_link_func,
        construct_queue_menu_func,
        play_next_func
    )
    
    return f"Playing: {query}"

@tool
def play(query: str, runtime: ToolRuntime[Context]) -> str:
    """Play a song or first 10 songs of a playlist from a given query (query can be url or title).
    If the query is an url, it is automatically parsed. If the query is a title it will be searched on youtube."""
    context = runtime.context
    interaction = context.interaction
    
    async def defer_and_play():
        if not interaction.response.is_done():
            await interaction.response.defer()
        return await _play_async(interaction, query)
    
    loop = interaction.client.loop
    coro = defer_and_play()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=30)
    except Exception as e:
        return f"Error playing: {str(e)}"
    