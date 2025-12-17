from dataclasses import dataclass
from typing import Optional
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

async def _play_async(interaction_or_message, query: str, n: int = 1) -> str:
    cog = get_music_bot_cog(interaction_or_message)
    if not cog:
        return "Error: MusicBot cog not found"
    
    state = global_state
    db = cog.db
    
    async def resolve_link_func(voice_id, link, n=1):
        return await resolve_link_for_guild(voice_id, link, cog.bot.loop, state, n)
    
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
    
    result = await play_logic(
        interaction,
        query,
        state,
        db,
        resolve_link_func,
        construct_queue_menu_func,
        play_next_func,
        n
    )
    
    return result or f"Playing: {query}"

@tool
def play(query: str, n: int = 1, *, runtime: ToolRuntime[Context]) -> str:
    """Play a song or multiple songs from a given query (query can be url or title).
    If the query is an url, it is automatically parsed. If the query is a title it will be searched on youtube.
    Params:
    - query: the query to search for or the url
    - n: the number of songs to play from the query or playlist (This is default to 1 instead explicitly specified by the user). For single URLs, this parameter is ignored."""
    context = runtime.context
    interaction = context.interaction
    message = context.message
    
    if interaction is None and message is None:
        return "Error: No interaction or message provided"
    
    interaction_or_message = interaction if interaction is not None else message
    
    async def defer_and_play():
        if interaction and not interaction.response.is_done():
            await interaction.response.defer()
        return await _play_async(interaction_or_message, query, n)
    
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

async def _skip_async(interaction_or_message, skip_i: Optional[int] = None, skip_to_j: Optional[int] = None) -> str:
    cog = get_music_bot_cog(interaction_or_message)
    if not cog:
        return "Error: MusicBot cog not found"
    
    state = global_state
    interaction = get_interaction(interaction_or_message)
    
    guild = interaction.guild
    if not guild:
        return "Error: No guild found"
    
    await skip_logic(interaction, state, guild.id, skip_i=skip_i, skip_to_j=skip_to_j)
    
    if skip_to_j is not None:
        return f"Skipped to song {skip_to_j} in queue"
    elif skip_i is not None:
        return f"Skipped {skip_i} song(s)"
    else:
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
    
    async def resolve_link_func(voice_id, link, n=1):
        return await resolve_link_for_guild(voice_id, link, cog.bot.loop, state, n)
    
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
def skip(skip_i: Optional[int] = None, skip_to_j: Optional[int] = None, *, runtime: ToolRuntime[Context]) -> str:
    """Skip song(s) in the playback queue.
    
    This tool allows you to skip the current song or multiple songs in different ways:
    
    Parameters (all optional):
    - skip_i: Number of songs to skip forward. If provided, skips i songs total (including the current one).
      Examples: skip_i=1 skips only the current song (default behavior), skip_i=2 skips current + 1 more, 
      skip_i=3 skips current + 2 more, etc. Must be a positive integer (>= 1).
    
    - skip_to_j: Position in queue to skip to. If provided, skips directly to the j-th song in the queue.
      The queue is 1-indexed, meaning j=1 refers to the first song in queue (next up), j=2 refers to the 
      second song, etc. This parameter takes precedence over skip_i if both are provided. Must be a 
      positive integer (>= 1) and cannot exceed the number of songs in the queue.
    
    Behavior:
    - If neither parameter is provided: Skips only the current song (default behavior, same as skip_i=1).
    - If skip_to_j is provided: Removes all songs before position j from the queue, then skips to that position.
    - If only skip_i is provided: Removes (skip_i - 1) songs from the queue, then skips the current song.
    - If both are provided: skip_to_j takes precedence and skip_i is ignored.
    
    Examples:
    - skip() or skip(skip_i=1): Skip the current song, play the next one in queue.
    - skip(skip_i=3): Skip the current song and the next 2 songs, play the 4th song.
    - skip(skip_to_j=1): Skip to the first song in queue (next up).
    - skip(skip_to_j=5): Skip to the 5th song in queue, removing songs at positions 1-4.
    
    Note: If there are not enough songs in the queue to fulfill the request, an error message will be returned."""
    
    context = runtime.context
    interaction = context.interaction
    message = context.message
    
    if interaction is None and message is None:
        return "Error: No interaction or message provided"
    
    interaction_or_message = interaction if interaction is not None else message
    
    async def defer_and_skip():
        if interaction and not interaction.response.is_done():
            await interaction.response.defer()
        return await _skip_async(interaction_or_message, skip_i=skip_i, skip_to_j=skip_to_j)
    
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

async def _get_queue_async(interaction_or_message) -> str:
    cog = get_music_bot_cog(interaction_or_message)
    if not cog:
        return "Error: MusicBot cog not found"
    
    state = global_state
    interaction = get_interaction(interaction_or_message)
    
    guild = interaction.guild
    if not guild:
        return "Error: No guild found"
    
    guild_id = guild.id
    queue = state.get_queue(guild_id)
    
    voice_client = guild.voice_client
    current_song = None
    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        current_source = voice_client.source
        if hasattr(current_source, 'data'):
            current_song = current_source.data.get('title', 'Unknown')
    
    if not current_song and len(queue) == 0:
        return "Queue is empty. No songs are currently playing or queued."
    
    result_parts = []
    if current_song:
        result_parts.append(f"Now playing: {current_song}")
    
    if len(queue) > 0:
        queue_list = []
        for i, song in enumerate(queue, start=1):
            title = song.data.get('title', 'Unknown') if hasattr(song, 'data') else 'Unknown'
            queue_list.append(f"{i}. {title}")
        result_parts.append(f"Queue ({len(queue)} song(s)):\n" + "\n".join(queue_list))
    else:
        result_parts.append("Queue is empty")
    
    return "\n\n".join(result_parts)

@tool
def get_queue(runtime: ToolRuntime[Context]) -> str:
    """Get the current song queue.
    Returns information about the currently playing song (if any) and all songs in the queue.
    Params:
    - No parameters required"""
    context = runtime.context
    interaction = context.interaction
    message = context.message
    
    if interaction is None and message is None:
        return "Error: No interaction or message provided"
    
    interaction_or_message = interaction if interaction is not None else message
    
    async def defer_and_get_queue():
        if interaction and not interaction.response.is_done():
            await interaction.response.defer()
        return await _get_queue_async(interaction_or_message)
    
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
    
    coro = defer_and_get_queue()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=30)
    except Exception as e:
        return f"Error getting queue: {str(e)}"
    