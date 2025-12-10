import logging
import asyncio
import time
import random
from collections import defaultdict

import discord
from discord.ext import commands, tasks
from discord import app_commands

from .audio import YoutubeDLAudioSource
from .utils import construct_log, validate_url, parse_duration, create_progress_bar, format_duration
from .database import PlaylistDatabase

logger = logging.getLogger(__name__)

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

    async def __resolve_link(self, voice_id, link):
        """Classify a link. Return a list of discord.PCMVolumeTransformer objects"""
        if voice_id not in self.queue_dict:
            self.queue_dict[voice_id] = []

        validated_link = validate_url(link)
        songs = await YoutubeDLAudioSource.from_url(validated_link, loop=self.bot.loop, stream=False)
        for song in songs:
            if not song.data.get('url'):
                song.data['url'] = link
                song.url = link
        self.queue_dict[voice_id] += songs
        return songs

    async def __play_next(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        guild = interaction.guild
        if not guild:
            return
        
        guild_id = guild.id
        if len(self.queue_dict.get(guild_id, [])) == 0:
            voice_client = guild.voice_client
            if voice_client and not voice_client.is_playing() and not voice_client.is_paused():
                self.idle_start_time[guild_id] = time.time()
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
        
        embed = await self.__construct_player_embed(interaction, song=song)
        view = PlayerView(self, interaction)
        
        voice_client = guild.voice_client
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.emoji in ['⏸️', '▶️']:
                if voice_client and voice_client.is_paused():
                    item.emoji = '▶️'
                else:
                    item.emoji = '⏸️'

        message = await interaction.followup.send(embed=embed, view=view)
        self.player_messages[guild_id] = {
            'message': message,
            'interaction': interaction
        }

        def after_play(error):
            if guild_id in self.player_messages:
                del self.player_messages[guild_id]
            coro = self.__play_next(interaction, channel)
            fut = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            try:
                fut.result()
            except:
                pass

        voice_client.play(song, after=after_play)

    async def __construct_media_buttons(self, interaction, metadata):
        """Construct media buttons for the user to control the medias"""
        embed = discord.Embed()
        embed.add_field(name="🎶🎶🎶   Now playing   🎶🎶🎶", value=metadata['title'], inline=False)
        embed.add_field(name="Length", value=metadata['duration'], inline=False)

        return MediaControlView({
            'Pause': self._pause_logic,
            'Resume': self._resume_logic,
            'Skip': self._skip_logic
        }, interaction), embed
    
    async def __construct_queue_menu(self, interaction):
        """Construct a menu for the user to control the queue"""
        embed = discord.Embed(title="📃   Danh sách chờ   📃")
        guild = interaction.guild
        if not guild:
            return None, embed
        
        voice_client = guild.voice_client
        guild_id = guild.id

        if voice_client and voice_client.is_playing():
            current_source = voice_client.source
            embed.add_field(name="Now playing", value=current_source.data['title'], inline=False)

        if len(self.queue_dict.get(guild_id, [])) > 0:
            embed.add_field(name="Next up", value=self.queue_dict[guild_id][0].data['title'], inline=False)

        if len(self.queue_dict.get(guild_id, [])) > 1:
            embed.add_field(name="Queue", value="\n".join([f"{i+1}. {song.data['title']}" for i, song in enumerate(self.queue_dict[guild_id][1:])]), inline=False)

        return MediaControlView({
            'Pause': self._pause_logic,
            'Resume': self._resume_logic,
            'Skip': self._skip_logic
        }, interaction), embed

    @app_commands.command(name='play', description='Hát')
    @app_commands.describe(url='URL hoặc tên bài hát (hoặc "personal" để phát playlist)')
    async def commands_play(self, interaction: discord.Interaction, url: str = None):
        """Add links from the user to the queue"""
        await interaction.response.defer()
        
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
            return
        
        server_id = guild.id

        if url is None and server_id not in self.queue_dict:
            await interaction.followup.send(embed=discord.Embed(description="Không có link thì tao hát cái gì?"))
            return

        joined = await self.join(interaction)
        if not joined:
            return
        
        if url and url.lower() in ['personal', 'playlist']:
            if not self.db.pool:
                await interaction.followup.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
                return
            
            user_id = interaction.user.id
            playlist_urls = await self.db.get_playlist_urls(user_id)
            if not playlist_urls:
                await interaction.followup.send(embed=discord.Embed(description="Playlist của bạn trống"))
                return
            
            songs = []
            for playlist_url in playlist_urls:
                try:
                    resolved_songs = await self.__resolve_link(guild.id, playlist_url)
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
            songs = await self.__resolve_link(guild.id, url)
        
        guild_id = guild.id
        if guild_id in self.idle_start_time:
            del self.idle_start_time[guild_id]
        
        songs_count = len(songs)
        voice_client = guild.voice_client
        if len(self.queue_dict.get(server_id, [])) - songs_count + 1 if voice_client and voice_client.is_playing() else 0 > 0:
            if songs_count == 1:
                await interaction.followup.send(embed=discord.Embed(description=f"Đã thêm **{songs[0].data['title']}**"))
            else:
                tracks_list = "\n".join([f"{i+1}. {song.data['title']}" for i, song in enumerate(songs)])
                embed = discord.Embed(
                    title=f"Đã thêm {songs_count} bài hát vào hàng chờ",
                    description=tracks_list
                )
                await interaction.followup.send(embed=embed)
            menu, embed = await self.__construct_queue_menu(interaction)
            if menu:
                await interaction.followup.send(embed=embed, view=menu)

        if voice_client and voice_client.is_playing():
            return
        
        try:
            await self.__play_next(interaction)
        except Exception as e:
            logger.error(f"Error in __play_next: {e}")
            await interaction.followup.send(embed=discord.Embed(description="Lỗi đ gì ý???"))

    async def _skip_logic(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
            return
        
        voice_client = guild.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.stop()
            server_id = guild.id
            if len(self.queue_dict.get(server_id, [])) > 0:
                await self.__play_next(interaction)
            else:
                await interaction.followup.send(embed=discord.Embed(description="Hết mẹ bài hát rồi còn đâu"))
        else:
            await interaction.followup.send(embed=discord.Embed(description="Có đang hát đéo đâu mà skip?"))

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
        
        voice_client = guild.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.pause()
            self.pause_start_time[guild.id] = time.time()
            await interaction.followup.send(embed=discord.Embed(description="Đã tạm dừng"))
        else:
            await interaction.followup.send(embed=discord.Embed(description="Có đang hát đéo đâu mà pause?"))

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
        
        voice_client = guild.voice_client
        if voice_client and voice_client.is_paused():
            voice_client.resume()
            guild_id = guild.id
            if guild_id in self.pause_start_time:
                paused_duration = time.time() - self.pause_start_time[guild_id]
                if guild_id not in self.total_paused_time:
                    self.total_paused_time[guild_id] = 0
                self.total_paused_time[guild_id] += paused_duration
                del self.pause_start_time[guild_id]
            await interaction.followup.send(embed=discord.Embed(description="Đã tiếp tục"))
        else:
            await interaction.followup.send(embed=discord.Embed(description="Có đang hát đéo đâu mà resume?"))

    @app_commands.command(name='resume', description='Tiếp tục bài hát')
    async def commands_resume(self, interaction: discord.Interaction):
        """Resume the current song"""
        await interaction.response.defer()
        await self._resume_logic(interaction)

    @app_commands.command(name='queue', description='Xem danh sách chờ')
    async def commands_queue(self, interaction: discord.Interaction):
        """Show the current queue"""
        await interaction.response.defer()
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
            return
        
        if len(self.queue_dict.get(guild.id, [])) == 0:
            await interaction.followup.send(embed=discord.Embed(description="Hàng chờ đéo có gì cả"))
            return

        menu, embed = await self.__construct_queue_menu(interaction)
        if menu:
            await interaction.followup.send(embed=embed, view=menu)
        else:
            await interaction.followup.send(embed=embed)

    @app_commands.command(name='clear', description='Xóa danh sách chờ')
    async def commands_clear(self, interaction: discord.Interaction):
        """Clear the current queue"""
        await interaction.response.defer()
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
            return
        
        self.queue_dict[guild.id] = []
        await interaction.followup.send(embed=discord.Embed(description="Đã xóa hết hàng chờ"))

    @app_commands.command(name='stop', description='Dừng bài hát')
    async def commands_stop(self, interaction: discord.Interaction):
        """Stop the current song"""
        await interaction.response.defer()
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

    async def __construct_player_embed(self, interaction: discord.Interaction, song=None):
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
        if guild_id in self.playback_start_time:
            base_elapsed = time.time() - self.playback_start_time[guild_id]
            total_paused = self.total_paused_time.get(guild_id, 0)
            
            if voice_client and voice_client.is_paused() and guild_id in self.pause_start_time:
                current_pause_duration = time.time() - self.pause_start_time[guild_id]
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

        queue = self.queue_dict.get(guild_id, [])
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
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
            return
        
        voice_client = guild.voice_client
        if not voice_client or (not voice_client.is_playing() and not voice_client.is_paused()):
            await interaction.followup.send(embed=discord.Embed(description="Không có bài hát nào đang phát"))
            return

        embed = await self.__construct_player_embed(interaction)
        view = PlayerView(self, interaction)
        
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.emoji in ['⏸️', '▶️']:
                if voice_client.is_paused():
                    item.emoji = '▶️'
                else:
                    item.emoji = '⏸️'

        message = await interaction.followup.send(embed=embed, view=view)
        self.player_messages[guild.id] = {
            'message': message,
            'interaction': interaction
        }

    @app_commands.command(name='playlist', description='Xem playlist cá nhân')
    async def commands_playlist(self, interaction: discord.Interaction):
        """View user's personal playlist"""
        await interaction.response.defer()
        if not self.db.pool:
            await interaction.followup.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
            return
        
        user_id = interaction.user.id
        playlist = await self.db.get_playlist(user_id)
        
        if not playlist:
            await interaction.followup.send(embed=discord.Embed(description="Playlist của bạn trống"))
            return
        
        embed = discord.Embed(title="📋 Playlist cá nhân")
        tracks_list = "\n".join([f"{i+1}. {item.get('title', 'Unknown')} - {item.get('url', '')}" for i, item in enumerate(playlist)])
        embed.description = tracks_list
        await interaction.followup.send(embed=embed)

    @app_commands.command(name='add', description='Thêm bài hát vào playlist')
    @app_commands.describe(url='URL hoặc tên bài hát')
    async def commands_add(self, interaction: discord.Interaction, url: str):
        """Add a song to user's personal playlist"""
        await interaction.response.defer()
        if not self.db.pool:
            await interaction.followup.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
            return
        
        if not url:
            await interaction.followup.send(embed=discord.Embed(description="Cần cung cấp URL hoặc tên bài hát"))
            return
        
        user_id = interaction.user.id
        validated_url = validate_url(url)
        
        try:
            songs = await YoutubeDLAudioSource.from_url(validated_url, loop=self.bot.loop, stream=False)
            if not songs:
                await interaction.followup.send(embed=discord.Embed(description="Không tìm thấy bài hát"))
                return
            
            song_title = songs[0].data.get('title', 'Unknown') if songs else 'Unknown'
            if len(songs) > 1:
                song_title = f"{song_title} (và {len(songs) - 1} bài khác)"
            
            success = await self.db.add_song(user_id, validated_url, song_title)
            
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

    @app_commands.command(name='remove', description='Xóa bài hát khỏi playlist')
    @app_commands.describe(identifier='Số thứ tự, URL hoặc tên bài hát')
    async def commands_remove(self, interaction: discord.Interaction, identifier: str):
        """Remove a song from user's personal playlist"""
        await interaction.response.defer()
        if not self.db.pool:
            await interaction.followup.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
            return
        
        if not identifier:
            await interaction.followup.send(embed=discord.Embed(description="Cần cung cấp số thứ tự, URL hoặc tên bài hát"))
            return
        
        user_id = interaction.user.id
        success = await self.db.remove_song(user_id, identifier)
        
        if success:
            await interaction.followup.send(embed=discord.Embed(description="Đã xóa bài hát khỏi playlist"))
        else:
            await interaction.followup.send(embed=discord.Embed(description="Không tìm thấy bài hát trong playlist"))

    @app_commands.command(name='play-playlist', description='Phát playlist cá nhân')
    async def commands_play_playlist(self, interaction: discord.Interaction):
        """Play user's personal playlist"""
        await self.commands_play.callback(self, interaction, url="personal")

    @app_commands.command(name='random', description='Phát bài hát ngẫu nhiên từ lịch sử của server')
    @app_commands.describe(number_of_urls='Số lượng bài hát muốn phát (mặc định: 1)')
    async def commands_random(self, interaction: discord.Interaction, number_of_urls: int = 1):
        """Play random songs from guild's play history"""
        await interaction.response.defer()
        
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=discord.Embed(description="Lỗi: Không tìm thấy server"))
            return
        
        if not self.db.pool:
            await interaction.followup.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
            return
        
        if number_of_urls < 1:
            await interaction.followup.send(embed=discord.Embed(description="Số lượng phải lớn hơn 0"))
            return
        
        if number_of_urls > 10:
            number_of_urls = 10
        
        guild_id = guild.id
        random_urls = await self.db.get_random_urls_from_history(guild_id, number_of_urls)
        
        if not random_urls:
            await interaction.followup.send(embed=discord.Embed(description="Không có lịch sử phát nhạc trong server này"))
            return
        
        joined = await self.join(interaction)
        if not joined:
            return
        
        songs = []
        for url_data in random_urls:
            url = url_data.get('url')
            if url:
                try:
                    resolved_songs = await self.__resolve_link(guild.id, url)
                    songs.extend(resolved_songs)
                except Exception as e:
                    logger.error(f"Error resolving random URL {url}: {e}")
                    continue
        
        if not songs:
            await interaction.followup.send(embed=discord.Embed(description="Không thể tải bài hát từ lịch sử"))
            return
        
        guild_id = guild.id
        if guild_id in self.idle_start_time:
            del self.idle_start_time[guild_id]
        
        songs_count = len(songs)
        voice_client = guild.voice_client
        if len(self.queue_dict.get(guild_id, [])) - songs_count + 1 if voice_client and voice_client.is_playing() else 0 > 0:
            if songs_count == 1:
                await interaction.followup.send(embed=discord.Embed(description=f"Đã thêm **{songs[0].data['title']}** từ lịch sử"))
            else:
                tracks_list = "\n".join([f"{i+1}. {song.data['title']}" for i, song in enumerate(songs)])
                embed = discord.Embed(
                    title=f"Đã thêm {songs_count} bài hát từ lịch sử vào hàng chờ",
                    description=tracks_list
                )
                await interaction.followup.send(embed=embed)
            menu, embed = await self.__construct_queue_menu(interaction)
            if menu:
                await interaction.followup.send(embed=embed, view=menu)

        if voice_client and voice_client.is_playing():
            return
        
        try:
            await self.__play_next(interaction)
        except Exception as e:
            logger.error(f"Error in __play_next: {e}")
            await interaction.followup.send(embed=discord.Embed(description="Lỗi đ gì ý???"))
