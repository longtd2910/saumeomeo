import time
import logging
from typing import Dict, Optional, Callable

import discord

from .utils import resolve_link, validate_url, join_voice_channel
from .audio import YoutubeDLAudioSource

logger = logging.getLogger(__name__)

async def skip_logic(
    interaction: discord.Interaction,
    queue_dict: Dict,
    guild_id: int
):
    guild = interaction.guild
    if not guild:
        await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
        return
    
    voice_client = guild.voice_client
    if voice_client and voice_client.is_playing():
        has_next = len(queue_dict.get(guild_id, [])) > 0
        voice_client.stop()
        if not has_next:
            await interaction.followup.send(embed=discord.Embed(description="Hết mẹ bài hát rồi còn đâu"))
    else:
        await interaction.followup.send(embed=discord.Embed(description="Có đang hát đéo đâu mà skip?"))

async def pause_logic(
    interaction: discord.Interaction,
    pause_start_time: Dict,
    guild_id: int
):
    guild = interaction.guild
    if not guild:
        await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
        return
    
    voice_client = guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        pause_start_time[guild_id] = time.time()
        await interaction.followup.send(embed=discord.Embed(description="Đã tạm dừng"))
    else:
        await interaction.followup.send(embed=discord.Embed(description="Có đang hát đéo đâu mà pause?"))

async def resume_logic(
    interaction: discord.Interaction,
    pause_start_time: Dict,
    total_paused_time: Dict,
    guild_id: int
):
    guild = interaction.guild
    if not guild:
        await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
        return
    
    voice_client = guild.voice_client
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        if guild_id in pause_start_time:
            paused_duration = time.time() - pause_start_time[guild_id]
            if guild_id not in total_paused_time:
                total_paused_time[guild_id] = 0
            total_paused_time[guild_id] += paused_duration
            del pause_start_time[guild_id]
        await interaction.followup.send(embed=discord.Embed(description="Đã tiếp tục"))
    else:
        await interaction.followup.send(embed=discord.Embed(description="Có đang hát đéo đâu mà resume?"))

async def resolve_link_for_guild(
    voice_id: int,
    link: str,
    loop,
    queue_dict: Dict
):
    return await resolve_link(link, loop, queue_dict, voice_id)

async def play_logic(
    interaction: discord.Interaction,
    url: Optional[str],
    queue_dict: Dict,
    db,
    resolve_link_func: Callable,
    construct_queue_menu_func: Callable,
    play_next_func: Callable,
    idle_start_time: Dict
):
    guild = interaction.guild
    if not guild:
        await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
        return
    
    server_id = guild.id

    if url is None and server_id not in queue_dict:
        await interaction.followup.send(embed=discord.Embed(description="Không có link thì tao hát cái gì?"))
        return

    joined = await join_voice_channel(interaction)
    if not joined:
        return
    
    if url and url.lower() in ['personal', 'playlist']:
        if not db.pool:
            await interaction.followup.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
            return
        
        user_id = interaction.user.id
        playlist_urls = await db.get_playlist_urls(user_id)
        if not playlist_urls:
            await interaction.followup.send(embed=discord.Embed(description="Playlist của bạn trống"))
            return
        
        songs = []
        for playlist_url in playlist_urls:
            try:
                resolved_songs = await resolve_link_func(guild.id, playlist_url)
                songs.extend(resolved_songs)
            except Exception as e:
                logger.error(f"Error resolving playlist URL {playlist_url}: {e}")
                continue
        
        if not songs:
            await interaction.followup.send(embed=discord.Embed(description="Không thể tải bài hát từ playlist"))
            return
    else:
        if not url:
            await interaction.followup.send(embed=discord.Embed(description="Không có link thì tao hát cái gì?"))
            return
        songs = await resolve_link_func(guild.id, url)
    
    guild_id = guild.id
    if guild_id in idle_start_time:
        del idle_start_time[guild_id]
    
    songs_count = len(songs)
    voice_client = guild.voice_client
    if len(queue_dict.get(server_id, [])) - songs_count + 1 if voice_client and voice_client.is_playing() else 0 > 0:
        if songs_count == 1:
            await interaction.followup.send(embed=discord.Embed(description=f"Đã thêm **{songs[0].data['title']}**"))
        else:
            tracks_list = "\n".join([f"{i+1}. {song.data['title']}" for i, song in enumerate(songs)])
            embed = discord.Embed(
                title=f"Đã thêm {songs_count} bài hát vào hàng chờ",
                description=tracks_list
            )
            await interaction.followup.send(embed=embed)
        menu, embed = await construct_queue_menu_func(interaction)
        if menu:
            await interaction.followup.send(embed=embed, view=menu)

    if voice_client and voice_client.is_playing():
        return
    
    try:
        await play_next_func(interaction)
    except Exception as e:
        logger.error(f"Error in play_next: {e}")
        await interaction.followup.send(embed=discord.Embed(description="Lỗi đ gì ý???"))

async def queue_logic(
    interaction: discord.Interaction,
    queue_dict: Dict,
    construct_queue_menu_func: Callable
):
    guild = interaction.guild
    if not guild:
        await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
        return
    
    if len(queue_dict.get(guild.id, [])) == 0:
        await interaction.followup.send(embed=discord.Embed(description="Hàng chờ đéo có gì cả"))
        return

    menu, embed = await construct_queue_menu_func(interaction)
    if menu:
        await interaction.followup.send(embed=embed, view=menu)
    else:
        await interaction.followup.send(embed=embed)

async def clear_logic(
    interaction: discord.Interaction,
    queue_dict: Dict,
    guild_id: int
):
    queue_dict[guild_id] = []
    await interaction.followup.send(embed=discord.Embed(description="Đã xóa hết hàng chờ"))

async def stop_logic(
    interaction: discord.Interaction,
    guild_id: int
):
    guild = interaction.guild
    if not guild:
        await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
        return
    
    voice_client = guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await interaction.followup.send(embed=discord.Embed(description="Đã dừng"))
    else:
        await interaction.followup.send(embed=discord.Embed(description="Có đang hát đéo đâu mà stop?"))

async def player_logic(
    interaction: discord.Interaction,
    queue_dict: Dict,
    playback_start_time: Dict,
    total_paused_time: Dict,
    pause_start_time: Dict,
    player_messages: Dict,
    construct_player_embed_func: Callable,
    player_view_factory: Callable
):
    guild = interaction.guild
    if not guild:
        await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
        return
    
    voice_client = guild.voice_client
    if not voice_client or (not voice_client.is_playing() and not voice_client.is_paused()):
        await interaction.followup.send(embed=discord.Embed(description="Không có bài hát nào đang phát"))
        return

    embed = await construct_player_embed_func(interaction)
    view = player_view_factory(interaction)
    
    for item in view.children:
        if isinstance(item, discord.ui.Button) and item.emoji in ['⏸️', '▶️']:
            if voice_client.is_paused():
                item.emoji = '▶️'
            else:
                item.emoji = '⏸️'

    message = await interaction.followup.send(embed=embed, view=view)
    player_messages[guild.id] = {
        'message': message,
        'interaction': interaction
    }

async def playlist_logic(
    interaction: discord.Interaction,
    db
):
    if not db.pool:
        await interaction.followup.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
        return
    
    user_id = interaction.user.id
    playlist = await db.get_playlist(user_id)
    
    if not playlist:
        await interaction.followup.send(embed=discord.Embed(description="Playlist của bạn trống"))
        return
    
    embed = discord.Embed(title="📋 Playlist cá nhân")
    tracks_list = "\n".join([f"{i+1}. {item.get('title', 'Unknown')} - {item.get('url', '')}" for i, item in enumerate(playlist)])
    embed.description = tracks_list
    await interaction.followup.send(embed=embed)

async def add_logic(
    interaction: discord.Interaction,
    url: str,
    db,
    loop
):
    if not db.pool:
        await interaction.followup.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
        return
    
    if not url:
        await interaction.followup.send(embed=discord.Embed(description="Cần cung cấp URL hoặc tên bài hát"))
        return
    
    user_id = interaction.user.id
    validated_url = validate_url(url)
    
    try:
        songs = await YoutubeDLAudioSource.from_url(validated_url, loop=loop, stream=False)
        if not songs:
            await interaction.followup.send(embed=discord.Embed(description="Không tìm thấy bài hát"))
            return
        
        song_title = songs[0].data.get('title', 'Unknown') if songs else 'Unknown'
        if len(songs) > 1:
            song_title = f"{song_title} (và {len(songs) - 1} bài khác)"
        
        success = await db.add_song(user_id, validated_url, song_title)
        
        if success:
            if len(songs) == 1:
                await interaction.followup.send(embed=discord.Embed(description=f"Đã thêm **{songs[0].data['title']}** vào playlist"))
            else:
                await interaction.followup.send(embed=discord.Embed(description=f"Đã thêm playlist ({len(songs)} bài hát) vào playlist cá nhân"))
        else:
            await interaction.followup.send(embed=discord.Embed(description="Bài hát đã có trong playlist"))
    except Exception as e:
        logger.error(f"Error adding song to playlist: {e}")
        await interaction.followup.send(embed=discord.Embed(description="Lỗi khi thêm bài hát vào playlist"))

async def remove_logic(
    interaction: discord.Interaction,
    identifier: str,
    db
):
    if not db.pool:
        await interaction.followup.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
        return
    
    if not identifier:
        await interaction.followup.send(embed=discord.Embed(description="Cần cung cấp số thứ tự, URL hoặc tên bài hát"))
        return
    
    user_id = interaction.user.id
    success = await db.remove_song(user_id, identifier)
    
    if success:
        await interaction.followup.send(embed=discord.Embed(description="Đã xóa bài hát khỏi playlist"))
    else:
        await interaction.followup.send(embed=discord.Embed(description="Không tìm thấy bài hát trong playlist"))

async def random_logic(
    interaction: discord.Interaction,
    number_of_urls: int,
    queue_dict: Dict,
    db,
    resolve_link_func: Callable,
    construct_queue_menu_func: Callable,
    play_next_func: Callable,
    idle_start_time: Dict
):
    guild = interaction.guild
    if not guild:
        await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
        return
    
    if not db.pool:
        await interaction.followup.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
        return
    
    if number_of_urls < 1:
        await interaction.followup.send(embed=discord.Embed(description="Số lượng phải lớn hơn 0"))
        return
    
    if number_of_urls > 10:
        number_of_urls = 10
    
    guild_id = guild.id
    random_urls = await db.get_random_urls_from_history(guild_id, number_of_urls)
    
    if not random_urls:
        await interaction.followup.send(embed=discord.Embed(description="Không có lịch sử phát nhạc trong server này"))
        return
    
    joined = await join_voice_channel(interaction)
    if not joined:
        return
    
    songs = []
    for url_data in random_urls:
        url = url_data.get('url')
        if url:
            try:
                resolved_songs = await resolve_link_func(guild.id, url)
                songs.extend(resolved_songs)
            except Exception as e:
                logger.error(f"Error resolving random URL {url}: {e}")
                continue
    
    if not songs:
        await interaction.followup.send(embed=discord.Embed(description="Không thể tải bài hát từ lịch sử"))
        return
    
    guild_id = guild.id
    if guild_id in idle_start_time:
        del idle_start_time[guild_id]
    
    songs_count = len(songs)
    voice_client = guild.voice_client
    if len(queue_dict.get(guild_id, [])) - songs_count + 1 if voice_client and voice_client.is_playing() else 0 > 0:
        if songs_count == 1:
            await interaction.followup.send(embed=discord.Embed(description=f"Đã thêm **{songs[0].data['title']}** từ lịch sử"))
        else:
            tracks_list = "\n".join([f"{i+1}. {song.data['title']}" for i, song in enumerate(songs)])
            embed = discord.Embed(
                title=f"Đã thêm {songs_count} bài hát từ lịch sử vào hàng chờ",
                description=tracks_list
            )
            await interaction.followup.send(embed=embed)
        menu, embed = await construct_queue_menu_func(interaction)
        if menu:
            await interaction.followup.send(embed=embed, view=menu)

    if voice_client and voice_client.is_playing():
        return
    
    try:
        await play_next_func(interaction)
    except Exception as e:
        logger.error(f"Error in play_next: {e}")
        await interaction.followup.send(embed=discord.Embed(description="Lỗi đ gì ý???"))

