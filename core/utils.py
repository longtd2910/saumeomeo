import time
from datetime import datetime
from urllib.parse import urlparse
from typing import Optional, Dict, List, Callable

import discord

def construct_log(log):
    return f"{datetime.now()} | {log}"

def validate_url(url, n=1):
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        return url
    if n > 1:
        return f"ytsearch{n}:{url}"
    return f"ytsearch:{url}"

def format_duration(duration: int):
    if duration < 3600:
        return "{:02d}:{:02d}".format(duration // 60, duration % 60)
    else:
        return "{:02d}:{:02d}:{:02d}".format(duration // 3600, (duration % 3600) // 60, duration % 60)

def parse_duration(duration_str: str) -> int:
    parts = duration_str.split(':')
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0

def create_progress_bar(current: int, total: int, length: int = 20) -> str:
    if total == 0:
        return '▱' * length
    filled = int((current / total) * length)
    bar = '▰' * filled + '▱' * (length - filled)
    return bar

async def join_voice_channel(interaction: discord.Interaction) -> bool:
    if not interaction.user.voice:
        if interaction.response.is_done():
            await interaction.followup.send(embed=discord.Embed(description="Không ở trong kênh thì vào hát kiểu lz gì?"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=discord.Embed(description="Không ở trong kênh thì vào hát kiểu lz gì?"), ephemeral=True)
        return False
    
    guild = interaction.guild
    voice_client = guild.voice_client if guild else None
    
    if voice_client is not None and voice_client.channel != interaction.user.voice.channel:
        if interaction.response.is_done():
            await interaction.followup.send(embed=discord.Embed(description="Tao đang hát ở chỗ khác rồi"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=discord.Embed(description="Tao đang hát ở chỗ khác rồi"), ephemeral=True)
        return False

    if voice_client is None:
        await interaction.user.voice.channel.connect()
    return True

async def resolve_link(link: str, loop, state, voice_id: int, n: int = 1):
    from .audio import YoutubeDLAudioSource
    
    validated_link = validate_url(link, n)
    songs = await YoutubeDLAudioSource.from_url(validated_link, loop=loop, stream=False, n=n)
    for song in songs:
        if not song.data.get('url'):
            song.data['url'] = link
            song.url = link
    queue = state.get_queue(voice_id)
    queue.extend(songs)
    return songs

def construct_player_embed(
    song: Optional[object],
    voice_client: Optional[discord.VoiceClient],
    state,
    guild_id: int,
    playback_start_time,
    total_paused_time,
    pause_start_time
) -> discord.Embed:
    embed = discord.Embed(title="🎵 Player", color=discord.Color.blue())
    
    if song:
        metadata = song.data
    elif voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        current_source = voice_client.source
        if not hasattr(current_source, 'data'):
            embed.description = "Không thể lấy thông tin bài hát"
            return embed
        metadata = current_source.data
    else:
        embed.description = "Không có bài hát nào đang phát"
        return embed

    title = metadata.get('title', 'Unknown')
    duration_str = metadata.get('duration', '00:00')
    total_seconds = parse_duration(duration_str)
    
    playback_start = playback_start_time.get_playback_start_time(guild_id)
    if playback_start:
        base_elapsed = time.time() - playback_start
        total_paused = total_paused_time.get_total_paused_time(guild_id)
        
        if voice_client and voice_client.is_paused():
            pause_start = pause_start_time.get_pause_start_time(guild_id)
            if pause_start:
                current_pause_duration = time.time() - pause_start
                total_paused += current_pause_duration
        
        elapsed = int(base_elapsed - total_paused)
    else:
        elapsed = 0

    if elapsed > total_seconds:
        elapsed = total_seconds

    elapsed_str = format_duration(elapsed) if elapsed >= 0 else "00:00"
    progress_bar = create_progress_bar(elapsed, total_seconds)
    
    status_emoji = "⏸️" if (voice_client and voice_client.is_paused()) else "▶️"
    
    description_parts = [
        f"{status_emoji}\t{title}",
        f"{elapsed_str}\t{progress_bar}\t{duration_str}"
    ]

    queue = state.get_queue(guild_id)
    if queue:
        next_songs = queue[:5]
        queue_text = "\n".join([f"{i+1}. {song.data.get('title', 'Unknown')}" for i, song in enumerate(next_songs)])
        if len(queue) > 5:
            queue_text += f"\n... và {len(queue) - 5} bài hát khác"
        description_parts.append(f"📋\tTiếp theo\n{queue_text}")
    else:
        description_parts.append("📋\tTiếp theo\nKhông có bài hát nào trong hàng chờ")

    embed.description = "\n\n".join(description_parts)

    return embed

def construct_queue_menu_embed(
    state,
    voice_client: Optional[discord.VoiceClient],
    guild_id: int
) -> discord.Embed:
    embed = discord.Embed(title="📃   Danh sách chờ   📃")

    if voice_client and voice_client.is_playing():
        current_source = voice_client.source
        embed.add_field(name="Now playing", value=current_source.data['title'], inline=False)

    queue = state.get_queue(guild_id)
    if len(queue) > 0:
        embed.add_field(name="Next up", value=queue[0].data['title'], inline=False)

    if len(queue) > 1:
        embed.add_field(name="Queue", value="\n".join([f"{i+1}. {song.data['title']}" for i, song in enumerate(queue[1:])]), inline=False)

    return embed

def construct_media_buttons_embed(metadata: Dict) -> discord.Embed:
    embed = discord.Embed()
    embed.add_field(name="🎶🎶🎶   Now playing   🎶🎶🎶", value=metadata['title'], inline=False)
    embed.add_field(name="Length", value=metadata['duration'], inline=False)
    return embed

async def skip_song_logic(
    voice_client: Optional[discord.VoiceClient],
    queue_dict: Dict,
    guild_id: int,
    interaction: discord.Interaction
) -> bool:
    if voice_client and voice_client.is_playing():
        has_next = len(queue_dict.get(guild_id, [])) > 0
        voice_client.stop()
        if not has_next:
            await interaction.followup.send(embed=discord.Embed(description="Hết mẹ bài hát rồi còn đâu"))
        return True
    else:
        await interaction.followup.send(embed=discord.Embed(description="Có đang hát đéo đâu mà skip?"))
        return False

async def pause_song_logic(
    voice_client: Optional[discord.VoiceClient],
    pause_start_time: Dict,
    guild_id: int,
    interaction: discord.Interaction
) -> bool:
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        pause_start_time[guild_id] = time.time()
        await interaction.followup.send(embed=discord.Embed(description="Đã tạm dừng"))
        return True
    else:
        await interaction.followup.send(embed=discord.Embed(description="Có đang hát đéo đâu mà pause?"))
        return False

async def resume_song_logic(
    voice_client: Optional[discord.VoiceClient],
    pause_start_time: Dict,
    total_paused_time: Dict,
    guild_id: int,
    interaction: discord.Interaction
) -> bool:
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        if guild_id in pause_start_time:
            paused_duration = time.time() - pause_start_time[guild_id]
            if guild_id not in total_paused_time:
                total_paused_time[guild_id] = 0
            total_paused_time[guild_id] += paused_duration
            del pause_start_time[guild_id]
        await interaction.followup.send(embed=discord.Embed(description="Đã tiếp tục"))
        return True
    else:
        await interaction.followup.send(embed=discord.Embed(description="Có đang hát đéo đâu mà resume?"))
        return False
    