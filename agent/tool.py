from dataclasses import dataclass
import discord
import asyncio
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime

from core.state import global_state
from core.database import PlaylistDatabase
from core.controller import play_logic, resolve_link_for_guild, skip_logic, pause_logic, resume_logic, random_logic
from core.view import construct_queue_menu

@dataclass
class Context:
    interaction: discord.Interaction = None
    message: discord.Message = None

def get_music_bot_cog(interaction_or_message):
    if isinstance(interaction_or_message, discord.Interaction):
        bot = interaction_or_message.client
    elif isinstance(interaction_or_message, discord.Message):
        bot = interaction_or_message._state._get_client()
        if bot is None and interaction_or_message.guild:
            bot = interaction_or_message.guild._state._get_client()
    else:
        return None
    if bot is None:
        return None
    cog = bot.get_cog('MusicBot')
    return cog

def get_interaction(interaction_or_message):
    if isinstance(interaction_or_message, discord.Message):
        message = interaction_or_message
        
        class FakeResponse:
            def __init__(self, channel):
                self._done = True
                self._channel = channel
            
            def is_done(self):
                return True
            
            async def send_message(self, *args, **kwargs):
                return await self._channel.send(*args, **kwargs)
        
        class FakeFollowup:
            def __init__(self, channel):
                self.channel = channel
            
            async def send(self, *args, **kwargs):
                ephemeral = kwargs.pop('ephemeral', False)
                return await self.channel.send(*args, **kwargs)
        
        class FakeInteraction:
            def __init__(self, message):
                self.guild = message.guild
                self.channel = message.channel
                self.user = message.author
                self.response = FakeResponse(message.channel)
                self.followup = FakeFollowup(message.channel)
            
            async def defer(self):
                pass
        
        return FakeInteraction(message)
    else:
        return interaction_or_message

async def _play_async(interaction_or_message, query: str) -> str:
    cog = get_music_bot_cog(interaction_or_message)
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
    
    interaction = get_interaction(interaction_or_message)
    
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
    If the query is an url, it is automatically parsed. If the query is a title it will be searched on youtube.
    Params:
    - query: the query to search for or the url"""
    context = runtime.context
    interaction = context.interaction
    message = context.message
    
    if interaction is None and message is None:
        return "Error: No interaction or message provided"
    
    interaction_or_message = interaction if interaction is not None else message
    
    async def defer_and_play():
        if interaction and not interaction.response.is_done():
            await interaction.response.defer()
        return await _play_async(interaction_or_message, query)
    
    if isinstance(interaction_or_message, discord.Interaction):
        loop = interaction_or_message.client.loop
    elif isinstance(interaction_or_message, discord.Message):
        bot = interaction_or_message._state._get_client()
        if bot is None and interaction_or_message.guild:
            bot = interaction_or_message.guild._state._get_client()
        if bot is None:
            return "Error: Could not get bot client from message"
        loop = bot.loop
    else:
        return "Error: Invalid interaction or message"
    
    coro = defer_and_play()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=30)
    except Exception as e:
        return f"Error playing: {str(e)}"

async def _skip_async(interaction_or_message) -> str:
    cog = get_music_bot_cog(interaction_or_message)
    if not cog:
        return "Error: MusicBot cog not found"
    
    state = global_state
    interaction = get_interaction(interaction_or_message)
    
    guild = interaction.guild
    if not guild:
        return "Error: No guild found"
    
    await skip_logic(interaction, state, guild.id)
    return "Skipped current song"

async def _pause_async(interaction_or_message) -> str:
    cog = get_music_bot_cog(interaction_or_message)
    if not cog:
        return "Error: MusicBot cog not found"
    
    state = global_state
    interaction = get_interaction(interaction_or_message)
    
    guild = interaction.guild
    if not guild:
        return "Error: No guild found"
    
    await pause_logic(interaction, state, guild.id)
    return "Paused playback"

async def _resume_async(interaction_or_message) -> str:
    cog = get_music_bot_cog(interaction_or_message)
    if not cog:
        return "Error: MusicBot cog not found"
    
    state = global_state
    interaction = get_interaction(interaction_or_message)
    
    guild = interaction.guild
    if not guild:
        return "Error: No guild found"
    
    await resume_logic(interaction, state, guild.id)
    return "Resumed playback"

async def _random_async(interaction_or_message, n: int) -> str:
    cog = get_music_bot_cog(interaction_or_message)
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
    
    interaction = get_interaction(interaction_or_message)
    
    await random_logic(
        interaction,
        n,
        state,
        db,
        resolve_link_func,
        construct_queue_menu_func,
        play_next_func
    )
    
    return f"Playing {n} random song(s) from history"

@tool
def skip(runtime: ToolRuntime[Context]) -> str:
    """Skip the current song that is playing.
    Stops the current playback and moves to the next song in the queue if available.
    Params:
    - No parameters required"""
    context = runtime.context
    interaction = context.interaction
    message = context.message
    
    if interaction is None and message is None:
        return "Error: No interaction or message provided"
    
    interaction_or_message = interaction if interaction is not None else message
    
    async def defer_and_skip():
        if interaction and not interaction.response.is_done():
            await interaction.response.defer()
        return await _skip_async(interaction_or_message)
    
    if isinstance(interaction_or_message, discord.Interaction):
        loop = interaction_or_message.client.loop
    elif isinstance(interaction_or_message, discord.Message):
        bot = interaction_or_message._state._get_client()
        if bot is None and interaction_or_message.guild:
            bot = interaction_or_message.guild._state._get_client()
        if bot is None:
            return "Error: Could not get bot client from message"
        loop = bot.loop
    else:
        return "Error: Invalid interaction or message"
    
    coro = defer_and_skip()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=30)
    except Exception as e:
        return f"Error skipping: {str(e)}"

@tool
def pause(runtime: ToolRuntime[Context]) -> str:
    """Pause the current song that is playing.
    Temporarily stops playback. Use resume to continue playing.
    Params:
    - No parameters required"""
    context = runtime.context
    interaction = context.interaction
    message = context.message
    
    if interaction is None and message is None:
        return "Error: No interaction or message provided"
    
    interaction_or_message = interaction if interaction is not None else message
    
    async def defer_and_pause():
        if interaction and not interaction.response.is_done():
            await interaction.response.defer()
        return await _pause_async(interaction_or_message)
    
    if isinstance(interaction_or_message, discord.Interaction):
        loop = interaction_or_message.client.loop
    elif isinstance(interaction_or_message, discord.Message):
        bot = interaction_or_message._state._get_client()
        if bot is None and interaction_or_message.guild:
            bot = interaction_or_message.guild._state._get_client()
        if bot is None:
            return "Error: Could not get bot client from message"
        loop = bot.loop
    else:
        return "Error: Invalid interaction or message"
    
    coro = defer_and_pause()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=30)
    except Exception as e:
        return f"Error pausing: {str(e)}"

@tool
def resume(runtime: ToolRuntime[Context]) -> str:
    """Resume the paused song.
    Continues playback from where it was paused.
    Params:
    - No parameters required"""
    context = runtime.context
    interaction = context.interaction
    message = context.message
    
    if interaction is None and message is None:
        return "Error: No interaction or message provided"
    
    interaction_or_message = interaction if interaction is not None else message
    
    async def defer_and_resume():
        if interaction and not interaction.response.is_done():
            await interaction.response.defer()
        return await _resume_async(interaction_or_message)
    
    if isinstance(interaction_or_message, discord.Interaction):
        loop = interaction_or_message.client.loop
    elif isinstance(interaction_or_message, discord.Message):
        bot = interaction_or_message._state._get_client()
        if bot is None and interaction_or_message.guild:
            bot = interaction_or_message.guild._state._get_client()
        if bot is None:
            return "Error: Could not get bot client from message"
        loop = bot.loop
    else:
        return "Error: Invalid interaction or message"
    
    coro = defer_and_resume()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=30)
    except Exception as e:
        return f"Error resuming: {str(e)}"

@tool
def random(n: int, runtime: ToolRuntime[Context]) -> str:
    """Play n random songs from the server's playback history.
    Selects random songs from previously played tracks in this server and adds them to the queue.
    Params:
    - n: the number of random songs to play (must be between 1 and 10, default is 1 if not specified)"""
    context = runtime.context
    interaction = context.interaction
    message = context.message
    
    if interaction is None and message is None:
        return "Error: No interaction or message provided"
    
    interaction_or_message = interaction if interaction is not None else message
    
    async def defer_and_random():
        if interaction and not interaction.response.is_done():
            await interaction.response.defer()
        return await _random_async(interaction_or_message, n)
    
    if isinstance(interaction_or_message, discord.Interaction):
        loop = interaction_or_message.client.loop
    elif isinstance(interaction_or_message, discord.Message):
        bot = interaction_or_message._state._get_client()
        if bot is None and interaction_or_message.guild:
            bot = interaction_or_message.guild._state._get_client()
        if bot is None:
            return "Error: Could not get bot client from message"
        loop = bot.loop
    else:
        return "Error: Invalid interaction or message"
    
    coro = defer_and_random()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=30)
    except Exception as e:
        return f"Error playing random: {str(e)}"
    