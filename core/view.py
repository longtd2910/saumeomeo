import time
import discord
from typing import Optional, Dict

from .utils import construct_queue_menu_embed, construct_media_buttons_embed, parse_duration, format_duration, create_progress_bar

class MediaControlView(discord.ui.View):
    def __init__(self, callbacks: dict[str, callable], interaction):
        super().__init__()
        self.callbacks = callbacks
        self.interaction = interaction

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='⏸️')
    async def pause_play(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.callbacks['Pause'](self.interaction)

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='▶️')
    async def resume_play(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.callbacks['Resume'](self.interaction)

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='⏭️')
    async def skip_play(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.callbacks['Skip'](self.interaction)
        message = await interaction.original_response()
        await message.edit(view=None)

class PlayerView(discord.ui.View):
    def __init__(self, bot_instance, interaction):
        super().__init__(timeout=None)
        self.bot_instance = bot_instance
        self.interaction = interaction

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='⏮️', row=0)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='⏸️', row=0)
    async def pause_resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        voice_client = guild.voice_client if guild else None
        if voice_client and voice_client.is_playing():
            voice_client.pause()
            self.bot_instance.pause_start_time[guild.id] = time.time()
            button.emoji = '▶️'
        elif voice_client and voice_client.is_paused():
            voice_client.resume()
            guild_id = guild.id
            if guild_id in self.bot_instance.pause_start_time:
                paused_duration = time.time() - self.bot_instance.pause_start_time[guild_id]
                if guild_id not in self.bot_instance.total_paused_time:
                    self.bot_instance.total_paused_time[guild_id] = 0
                self.bot_instance.total_paused_time[guild_id] += paused_duration
                del self.bot_instance.pause_start_time[guild_id]
            button.emoji = '⏸️'
        await interaction.response.edit_message(view=self)

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='⏭️', row=0)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.bot_instance._skip_logic(interaction)

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='⏹️', row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        voice_client = guild.voice_client if guild else None
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()
        await interaction.response.defer()

def construct_queue_menu(
    queue_dict: Dict,
    voice_client: Optional[discord.VoiceClient],
    guild_id: int,
    pause_callback,
    resume_callback,
    skip_callback,
    interaction: discord.Interaction
):
    embed = discord.Embed(title="📃   Danh sách chờ   📃")
    
    if voice_client and voice_client.is_playing():
        current_source = voice_client.source
        embed.add_field(name="Now playing", value=current_source.data['title'], inline=False)

    if len(queue_dict.get(guild_id, [])) > 0:
        embed.add_field(name="Next up", value=queue_dict[guild_id][0].data['title'], inline=False)

    if len(queue_dict.get(guild_id, [])) > 1:
        embed.add_field(name="Queue", value="\n".join([f"{i+1}. {song.data['title']}" for i, song in enumerate(queue_dict[guild_id][1:])]), inline=False)

    return MediaControlView({
        'Pause': pause_callback,
        'Resume': resume_callback,
        'Skip': skip_callback
    }, interaction), embed

def construct_media_buttons(metadata: Dict, pause_callback, resume_callback, skip_callback, interaction: discord.Interaction):
    embed = construct_media_buttons_embed(metadata)
    return MediaControlView({
        'Pause': pause_callback,
        'Resume': resume_callback,
        'Skip': skip_callback
    }, interaction), embed

async def construct_player_embed_for_interaction(
    interaction: discord.Interaction,
    song: Optional[object],
    queue_dict: Dict,
    playback_start_time: Dict,
    total_paused_time: Dict,
    pause_start_time: Dict
) -> discord.Embed:
    embed = discord.Embed(title="🎵 Player", color=discord.Color.blue())
    guild = interaction.guild
    if not guild:
        embed.description = "Không có bài hát nào đang phát"
        return embed
    
    voice_client = guild.voice_client
    
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
    
    guild_id = guild.id
    if guild_id in playback_start_time:
        base_elapsed = time.time() - playback_start_time[guild_id]
        total_paused = total_paused_time.get(guild_id, 0)
        
        if voice_client and voice_client.is_paused() and guild_id in pause_start_time:
            current_pause_duration = time.time() - pause_start_time[guild_id]
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

    queue = queue_dict.get(guild_id, [])
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

