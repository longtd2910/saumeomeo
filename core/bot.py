import logging
import asyncio
import time
import random
from collections import defaultdict

import discord
from discord.ext import commands, tasks
from discord import app_commands

from .audio import YoutubeDLAudioSource
from .utils import (
    construct_log, validate_url,
    join_voice_channel, construct_player_embed
)
from .database import PlaylistDatabase
from .view import MediaControlView, PlayerView, construct_queue_menu, construct_media_buttons, construct_player_embed_for_interaction
from .controller import (
    skip_logic, pause_logic, resume_logic, resolve_link_for_guild,
    play_logic, queue_logic, clear_logic, stop_logic, player_logic,
    playlist_logic, add_logic, remove_logic, random_logic
)

logger = logging.getLogger(__name__)


class MusicBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue_dict: defaultdict[list[discord.FFmpegPCMAudio]] = {}
        self.current_menu_dict: defaultdict = {}
        self.playback_start_time: defaultdict = {}
        self.pause_start_time: defaultdict = {}
        self.total_paused_time: defaultdict = {}
        self.player_messages: defaultdict = {}
        self.idle_start_time: defaultdict = {}
        self.db = PlaylistDatabase()
        self.update_player_task.start()
        self.idle_check_task.start()

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self.db.connect()
        except Exception as e:
            logger.error(f"Database connection failed: {e}. Playlist features will be unavailable.")
        logger.debug(construct_log(f'{self.bot.user} has connected to Discord!'))
        
        if self.db.pool:
            for guild in self.bot.guilds:
                await self.db.add_guild(guild.id)
            logger.debug(construct_log(f'Registered {len(self.bot.guilds)} existing guild(s)'))
            
            version = getattr(self.bot, 'app_version', None)
            change_note = getattr(self.bot, 'change_note', '')
            
            if version and not await self.db.is_version_announced(version):
                message_templates = [
                    f"Tao vừa được cập nhật. Nhìn chung là:\n{{change_note}}",
                    f"Update mới đây:\n{{change_note}}",
                    f"Tao mới update xong. Thay đổi:\n{{change_note}}",
                    f"Version mới ra lò:\n{{change_note}}",
                    f"Tao vừa nâng cấp. Các thay đổi:\n{{change_note}}",
                    f"Update time! Những gì mới:\n{{change_note}}",
                    f"Tao đã được cập nhật. Chi tiết:\n{{change_note}}",
                    f"Bản cập nhật mới:\n{{change_note}}"
                ]
                
                guild_ids = await self.db.get_all_guilds()
                for guild_id in guild_ids:
                    try:
                        guild = self.bot.get_guild(guild_id)
                        if not guild:
                            continue
                        
                        channel = guild.system_channel
                        if not channel:
                            text_channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages]
                            if text_channels:
                                channel = text_channels[0]
                        
                        if channel:
                            template = random.choice(message_templates)
                            message = template.format(change_note=change_note)
                            await channel.send(message)
                            logger.debug(construct_log(f'Sent version announcement to guild {guild_id}'))
                    except Exception as e:
                        logger.error(f"Error sending announcement to guild {guild_id}: {e}")
                
                await self.db.mark_version_announced(version)
                logger.debug(construct_log(f'Marked version {version} as announced'))
        
        await asyncio.sleep(1)
        
        try:
            all_commands = [cmd.name for cmd in self.bot.tree.get_commands()]
            logger.debug(construct_log(f'Commands in tree before sync: {", ".join(all_commands)}'))
            if 'random' not in all_commands:
                logger.warning(construct_log('WARNING: /random command not found in command tree before sync!'))
                for cmd in self.bot.tree.get_commands():
                    if hasattr(cmd, 'name'):
                        logger.debug(construct_log(f'Found command: {cmd.name}'))
            
            synced = await self.bot.tree.sync()
            synced_names = [cmd.name for cmd in synced]
            logger.debug(construct_log(f'Synced {len(synced)} command(s): {", ".join(synced_names)}'))
            if 'random' not in synced_names:
                logger.warning(construct_log('WARNING: /random command was not synced!'))
                logger.warning(construct_log('Attempting to force sync all commands...'))
                try:
                    await self.bot.tree.sync()
                except Exception as sync_error:
                    logger.error(construct_log(f'Force sync failed: {sync_error}'))
        except Exception as e:
            logger.error(construct_log(f'Failed to sync commands: {e}'))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        if self.db.pool:
            success = await self.db.add_guild(guild.id)
            if success:
                logger.info(f"Added guild {guild.id} ({guild.name}) to database")
            else:
                logger.error(f"Failed to add guild {guild.id} ({guild.name}) to database")
        else:
            logger.warning(f"Database not available, could not add guild {guild.id} ({guild.name})")

    def cog_unload(self):
        self.update_player_task.cancel()
        self.idle_check_task.cancel()
        if self.bot.loop and not self.bot.loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.db.close(), self.bot.loop)

    async def join(self, interaction: discord.Interaction):
        return await join_voice_channel(interaction)

    async def __resolve_link(self, voice_id, link):
        return await resolve_link_for_guild(voice_id, link, self.bot.loop, self.queue_dict)

    async def __play_next(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        guild = interaction.guild
        if not guild:
            return
        
        guild_id = guild.id
        if len(self.queue_dict.get(guild_id, [])) == 0:
            voice_client = guild.voice_client
            if voice_client and not voice_client.is_playing() and not voice_client.is_paused():
                self.idle_start_time[guild_id] = time.time()
            if guild_id in self.player_messages:
                del self.player_messages[guild_id]
            return
        
        if guild_id in self.idle_start_time:
            del self.idle_start_time[guild_id]
        
        song = self.queue_dict[guild_id].pop(0)

        if self.db.pool:
            url = song.data.get('url') or getattr(song, 'url', None)
            title = song.data.get('title', 'Unknown')
            if url:
                await self.db.log_played_url(guild_id, url, title)

        self.playback_start_time[guild_id] = time.time()
        self.total_paused_time[guild_id] = 0
        if guild_id in self.pause_start_time:
            del self.pause_start_time[guild_id]
        
        voice_client = guild.voice_client
        
        embed = construct_player_embed(
            song=song,
            voice_client=voice_client,
            queue_dict=self.queue_dict,
            guild_id=guild_id,
            playback_start_time=self.playback_start_time,
            total_paused_time=self.total_paused_time,
            pause_start_time=self.pause_start_time
        )
        view = PlayerView(self, interaction)
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.emoji in ['⏸️', '▶️']:
                if voice_client and voice_client.is_paused():
                    item.emoji = '▶️'
                else:
                    item.emoji = '⏸️'

        target_channel = channel or getattr(interaction, 'channel', None)
        message = None
        
        if guild_id in self.player_messages:
            existing_message_data = self.player_messages[guild_id]
            existing_message = existing_message_data.get('message')
            if existing_message:
                try:
                    await existing_message.edit(embed=embed, view=view)
                    message = existing_message
                    self.player_messages[guild_id] = {
                        'message': message,
                        'interaction': interaction
                    }
                except (discord.NotFound, discord.HTTPException):
                    if guild_id in self.player_messages:
                        del self.player_messages[guild_id]
        
        if not message:
            try:
                message = await interaction.followup.send(embed=embed, view=view)
            except Exception:
                if target_channel:
                    message = await target_channel.send(embed=embed, view=view)
            if message:
                self.player_messages[guild_id] = {
                    'message': message,
                    'interaction': interaction
                }

        def after_play(error):
            coro = self.__play_next(interaction, channel or getattr(interaction, 'channel', None))
            fut = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            try:
                fut.result()
            except:
                pass

        voice_client.play(song, after=after_play)

    async def __construct_media_buttons(self, interaction, metadata):
        return construct_media_buttons(
            metadata,
            self._pause_logic,
            self._resume_logic,
            self._skip_logic,
            interaction
        )
    
    async def __construct_queue_menu(self, interaction):
        guild = interaction.guild
        if not guild:
            embed = discord.Embed(title="📃   Danh sách chờ   📃")
            return None, embed
        
        voice_client = guild.voice_client
        guild_id = guild.id

        return construct_queue_menu(
            self.queue_dict,
            voice_client,
            guild_id,
            self._pause_logic,
            self._resume_logic,
            self._skip_logic,
            interaction
        )

    @app_commands.command(name='play', description='Hát')
    @app_commands.describe(url='URL hoặc tên bài hát (hoặc "personal" để phát playlist)')
    async def commands_play(self, interaction: discord.Interaction, url: str = None):
        await interaction.response.defer()
        await play_logic(
            interaction,
            url,
            self.queue_dict,
            self.db,
            self.__resolve_link,
            self.__construct_queue_menu,
            self.__play_next,
            self.idle_start_time
        )

    async def _skip_logic(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
            return
        await skip_logic(interaction, self.queue_dict, guild.id)

    @app_commands.command(name='skip', description='Bỏ qua bài hát')
    async def commands_skip(self, interaction: discord.Interaction):
        """Skip the current song"""
        await interaction.response.defer()
        await self._skip_logic(interaction)

    async def _pause_logic(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
            return
        await pause_logic(interaction, self.pause_start_time, guild.id)

    @app_commands.command(name='pause', description='Tạm dừng bài hát')
    async def commands_pause(self, interaction: discord.Interaction):
        """Pause the current song"""
        await interaction.response.defer()
        await self._pause_logic(interaction)

    async def _resume_logic(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
            return
        await resume_logic(interaction, self.pause_start_time, self.total_paused_time, guild.id)

    @app_commands.command(name='resume', description='Tiếp tục bài hát')
    async def commands_resume(self, interaction: discord.Interaction):
        """Resume the current song"""
        await interaction.response.defer()
        await self._resume_logic(interaction)

    @app_commands.command(name='queue', description='Xem danh sách chờ')
    async def commands_queue(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await queue_logic(
            interaction,
            self.queue_dict,
            self.__construct_queue_menu
        )

    @app_commands.command(name='clear', description='Xóa danh sách chờ')
    async def commands_clear(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
            return
        await clear_logic(interaction, self.queue_dict, guild.id)

    @app_commands.command(name='stop', description='Dừng bài hát')
    async def commands_stop(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
            return
        await stop_logic(interaction, guild.id)

    async def __construct_player_embed(self, interaction: discord.Interaction, song=None):
        return await construct_player_embed_for_interaction(
            interaction,
            song,
            self.queue_dict,
            self.playback_start_time,
            self.total_paused_time,
            self.pause_start_time
        )

    @tasks.loop(seconds=3.0)
    async def update_player_task(self):
        for guild_id, message_data in list(self.player_messages.items()):
            try:
                interaction = message_data['interaction']
                message = message_data['message']
                guild = interaction.guild
                
                if not guild:
                    if guild_id in self.player_messages:
                        del self.player_messages[guild_id]
                    continue
                
                voice_client = guild.voice_client
                if not voice_client or (not voice_client.is_playing() and not voice_client.is_paused()):
                    if guild_id in self.player_messages:
                        del self.player_messages[guild_id]
                    continue

                embed = await self.__construct_player_embed(interaction)
                view = PlayerView(self, interaction)
                
                for item in view.children:
                    if isinstance(item, discord.ui.Button) and item.emoji in ['⏸️', '▶️']:
                        if voice_client.is_paused():
                            item.emoji = '▶️'
                        else:
                            item.emoji = '⏸️'

                await message.edit(embed=embed, view=view)
            except (discord.NotFound, discord.HTTPException, AttributeError) as e:
                if guild_id in self.player_messages:
                    del self.player_messages[guild_id]

    @update_player_task.before_loop
    async def before_update_player_task(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=30.0)
    async def idle_check_task(self):
        current_time = time.time()
        for guild_id in list(self.idle_start_time.keys()):
            try:
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    if guild_id in self.idle_start_time:
                        del self.idle_start_time[guild_id]
                    continue
                
                voice_client = guild.voice_client
                if not voice_client:
                    if guild_id in self.idle_start_time:
                        del self.idle_start_time[guild_id]
                    continue
                
                queue = self.queue_dict.get(guild_id, [])
                is_playing = voice_client.is_playing() or voice_client.is_paused()
                
                if is_playing or len(queue) > 0:
                    if guild_id in self.idle_start_time:
                        del self.idle_start_time[guild_id]
                    continue
                
                idle_duration = current_time - self.idle_start_time[guild_id]
                if idle_duration >= 180:
                    await voice_client.disconnect()
                    if guild_id in self.idle_start_time:
                        del self.idle_start_time[guild_id]
                    logger.debug(construct_log(f"Disconnected from voice channel in guild {guild_id} after 3 minutes of idle"))
            except Exception as e:
                logger.error(construct_log(f"Error in idle check for guild {guild_id}: {e}"))
                if guild_id in self.idle_start_time:
                    del self.idle_start_time[guild_id]

    @idle_check_task.before_loop
    async def before_idle_check_task(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name='player', description='Hiển thị player với progress và danh sách chờ')
    async def commands_player(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await player_logic(
            interaction,
            self.queue_dict,
            self.playback_start_time,
            self.total_paused_time,
            self.pause_start_time,
            self.player_messages,
            self.__construct_player_embed,
            lambda inter: PlayerView(self, inter)
        )

    @app_commands.command(name='playlist', description='Xem playlist cá nhân')
    async def commands_playlist(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await playlist_logic(interaction, self.db)

    @app_commands.command(name='add', description='Thêm bài hát vào playlist')
    @app_commands.describe(url='URL hoặc tên bài hát')
    async def commands_add(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer()
        await add_logic(interaction, url, self.db, self.bot.loop)

    @app_commands.command(name='remove', description='Xóa bài hát khỏi playlist')
    @app_commands.describe(identifier='Số thứ tự, URL hoặc tên bài hát')
    async def commands_remove(self, interaction: discord.Interaction, identifier: str):
        await interaction.response.defer()
        await remove_logic(interaction, identifier, self.db)

    @app_commands.command(name='play-playlist', description='Phát playlist cá nhân')
    async def commands_play_playlist(self, interaction: discord.Interaction):
        """Play user's personal playlist"""
        await self.commands_play.callback(self, interaction, url="personal")

    @app_commands.command(name='random', description='Phát bài hát ngẫu nhiên từ lịch sử của server')
    @app_commands.describe(number_of_urls='Số lượng bài hát muốn phát (mặc định: 1)')
    async def commands_random(self, interaction: discord.Interaction, number_of_urls: int = 1):
        await interaction.response.defer()
        await random_logic(
            interaction,
            number_of_urls,
            self.queue_dict,
            self.db,
            self.__resolve_link,
            self.__construct_queue_menu,
            self.__play_next,
            self.idle_start_time
        )
