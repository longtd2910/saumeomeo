import time
import logging
from typing import Dict

import discord

from .utils import resolve_link

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

