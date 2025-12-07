import logging
import asyncio
import time
from collections import defaultdict

import discord
from discord.ext import commands, tasks

from .audio import YoutubeDLAudioSource
from .utils import construct_log, validate_url, parse_duration, create_progress_bar, format_duration
from .database import PlaylistDatabase

logger = logging.getLogger(__name__)

class MediaControlView(discord.ui.View):
    def __init__(self, callbacks: dict[str, callable], context):
        super().__init__()
        self.callbacks = callbacks
        self.context = context

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='⏸️')
    async def pause_play(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.callbacks['Pause'](self.context)
        await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='▶️')
    async def resume_play(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.callbacks['Resume'](self.context)
        await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='⏭️')
    async def skip_play(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.callbacks['Skip'](self.context)

        await interaction.response.defer()
        message = await interaction.original_response()
        await message.edit(view=None)

class PlayerView(discord.ui.View):
    def __init__(self, bot_instance, context):
        super().__init__(timeout=None)
        self.bot_instance = bot_instance
        self.context = context

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='⏮️', row=0)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='⏸️', row=0)
    async def pause_resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await self.context.bot.get_context(interaction.message)
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            self.bot_instance.pause_start_time[ctx.message.guild.id] = time.time()
            button.emoji = '▶️'
        elif ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            guild_id = ctx.message.guild.id
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
        ctx = await self.context.bot.get_context(interaction.message)
        await self.bot_instance.commands_skip(ctx)
        await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.grey, emoji='⏹️', row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await self.context.bot.get_context(interaction.message)
        if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
            ctx.voice_client.stop()
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

    def cog_unload(self):
        self.update_player_task.cancel()
        self.idle_check_task.cancel()
        if self.bot.loop and not self.bot.loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.db.close(), self.bot.loop)

    async def join(self, ctx):
        if not ctx.message.author.voice:
            await ctx.send(embed=discord.Embed(description="Không ở trong kênh thì vào hát kiểu lz gì?"))
            return
        
        if ctx.voice_client is not None and ctx.voice_client.channel != ctx.message.author.voice.channel:
            await ctx.send(embed=discord.Embed(description="Tao đang hát ở chỗ khác rồi"))

        if ctx.voice_client is None:
            await ctx.message.author.voice.channel.connect()

    async def __resolve_link(self, voice_id, link):
        """Classify a link. Return a list of discord.PCMVolumeTransformer objects"""
        if voice_id not in self.queue_dict:
            self.queue_dict[voice_id] = []

        songs = await YoutubeDLAudioSource.from_url(validate_url(link), loop=self.bot.loop, stream=False)
        self.queue_dict[voice_id] += songs
        return songs

    async def __play_next(self, ctx):
        if len(self.queue_dict[ctx.message.guild.id]) == 0:
            guild_id = ctx.message.guild.id
            if ctx.voice_client and not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
                self.idle_start_time[guild_id] = time.time()
            return
        
        guild_id = ctx.message.guild.id
        if guild_id in self.idle_start_time:
            del self.idle_start_time[guild_id]
        
        song = self.queue_dict[ctx.message.guild.id].pop(0)

        self.playback_start_time[guild_id] = time.time()
        self.total_paused_time[guild_id] = 0
        if guild_id in self.pause_start_time:
            del self.pause_start_time[guild_id]
        
        embed = await self.__construct_player_embed(ctx, song=song)
        view = PlayerView(self, ctx)
        
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.emoji in ['⏸️', '▶️']:
                if ctx.voice_client and ctx.voice_client.is_paused():
                    item.emoji = '▶️'
                else:
                    item.emoji = '⏸️'

        message = await ctx.send(embed=embed, view=view)
        self.player_messages[guild_id] = {
            'message': message,
            'context': ctx
        }

        def after_play(error):
            guild_id = ctx.message.guild.id
            if guild_id in self.player_messages:
                del self.player_messages[guild_id]
            coro = self.__play_next(ctx)
            fut = asyncio.run_coroutine_threadsafe(coro, ctx.bot.loop)
            try:
                fut.result()
            except:
                pass

        ctx.voice_client.play(song, after=after_play)

    async def __construct_media_buttons(self, ctx, metadata):
        """Construct media buttons for the user to control the medias"""
        embed = discord.Embed()
        embed.add_field(name="🎶🎶🎶   Now playing   🎶🎶🎶", value=metadata['title'], inline=False)
        embed.add_field(name="Length", value=metadata['duration'], inline=False)

        return MediaControlView({
            'Pause': self.commands_pause,
            'Resume': self.commands_resume,
            'Skip': self.commands_skip
        }, ctx), embed
    
    async def __construct_queue_menu(self, ctx):
        """Construct a menu for the user to control the queue"""
        embed = discord.Embed(title="📃   Danh sách chờ   📃")

        if ctx.voice_client.is_playing():
            current_source = ctx.voice_client.source
            embed.add_field(name="Now playing", value=current_source.data['title'], inline=False)

        if len(self.queue_dict[ctx.message.guild.id]) > 0:
            embed.add_field(name="Next up", value=self.queue_dict[ctx.message.guild.id][0].data['title'], inline=False)

        if len(self.queue_dict[ctx.message.guild.id]) > 1:
            embed.add_field(name="Queue", value="\n".join([f"{i+1}. {song.data['title']}" for i, song in enumerate(self.queue_dict[ctx.message.guild.id][1:])]), inline=False)

        return MediaControlView({
            'Pause': self.commands_pause,
            'Resume': self.commands_resume,
            'Skip': self.commands_skip
        }, ctx), embed

    @commands.command(name='play', help='Hát')
    async def commands_play(self, ctx, *, url):
        """Add links from the user to the queue"""
        server_id = ctx.message.guild.id

        if url is None and server_id not in self.queue_dict:
            await ctx.send(embed=discord.Embed(description="Không có link thì tao hát cái gì?"))
            return

        await self.join(ctx=ctx)
        
        if url.lower() in ['personal', 'playlist']:
            if not self.db.pool:
                await ctx.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
                return
            
            user_id = ctx.message.author.id
            playlist_urls = await self.db.get_playlist_urls(user_id)
            if not playlist_urls:
                await ctx.send(embed=discord.Embed(description="Playlist của bạn trống"))
                return
            
            songs = []
            for playlist_url in playlist_urls:
                try:
                    resolved_songs = await self.__resolve_link(ctx.message.guild.id, playlist_url)
                    songs.extend(resolved_songs)
                except Exception as e:
                    logger.error(f"Error resolving playlist URL {playlist_url}: {e}")
                    continue
            
            if not songs:
                await ctx.send(embed=discord.Embed(description="Không thể tải bài hát từ playlist"))
                return
        else:
            songs = await self.__resolve_link(ctx.message.guild.id, url)
        
        guild_id = ctx.message.guild.id
        if guild_id in self.idle_start_time:
            del self.idle_start_time[guild_id]
        
        songs_count = len(songs)
        if len(self.queue_dict[server_id]) - songs_count + 1 if ctx.voice_client.is_playing() else 0 > 0:
            if songs_count == 1:
                await ctx.send(embed=discord.Embed(description=f"Đã thêm **{songs[0].data['title']}**"))
            else:
                tracks_list = "\n".join([f"{i+1}. {song.data['title']}" for i, song in enumerate(songs)])
                embed = discord.Embed(
                    title=f"Đã thêm {songs_count} bài hát vào hàng chờ",
                    description=tracks_list
                )
                await ctx.send(embed=embed)
            menu, embed = await self.__construct_queue_menu(ctx)
            await ctx.send(embed=embed, view=menu)

        if ctx.voice_client.is_playing():
            return
        
        try:
            await self.__play_next(ctx)
        except:
            await ctx.send(embed=discord.Embed(description="Lỗi đ gì ý???"))

    @commands.command(name='skip', help='Bỏ qua bài hát')
    async def commands_skip(self, ctx):
        """Skip the current song"""
        if ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            server_id = ctx.message.guild.id
            if len(self.queue_dict[server_id]) > 0:
                await self.__play_next(ctx)
            else:
                await ctx.send(embed=discord.Embed(description="Hết mẹ bài hát rồi còn đâu"))
        else:
            await ctx.send(embed=discord.Embed(description="Có đang hát đéo đâu mà skip?"))

    @commands.command(name='pause', help='Tạm dừng bài hát')
    async def commands_pause(self, ctx):
        """Pause the current song"""
        if ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            self.pause_start_time[ctx.message.guild.id] = time.time()
        else:
            await ctx.send(embed=discord.Embed(description="Có đang hát đéo đâu mà pause?"))

    @commands.command(name='resume', help='Tiếp tục bài hát')
    async def commands_resume(self, ctx):
        """Resume the current song"""
        if ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            guild_id = ctx.message.guild.id
            if guild_id in self.pause_start_time:
                paused_duration = time.time() - self.pause_start_time[guild_id]
                if guild_id not in self.total_paused_time:
                    self.total_paused_time[guild_id] = 0
                self.total_paused_time[guild_id] += paused_duration
                del self.pause_start_time[guild_id]
        else:
            await ctx.send(embed=discord.Embed(description="Có đang hát đéo đâu mà resume?"))

    @commands.command(name='queue', help='Xem danh sách chờ')
    async def commands_queue(self, ctx):
        """Show the current queue"""
        if len(self.queue_dict[ctx.message.guild.id]) == 0:
            await ctx.send(embed=discord.Embed(description="Hàng chờ đéo có gì cả"))
            return

        menu, embed = await self.__construct_queue_menu(ctx)
        await ctx.send(embed=embed, view=menu)

    @commands.command(name='clear', help='Xóa danh sách chờ')
    async def commands_clear(self, ctx):
        """Clear the current queue"""
        self.queue_dict[ctx.message.guild.id] = []
        await ctx.send(embed=discord.Embed(description="Đã xóa hết hàng chờ"))

    @commands.command(name='stop', help='Dừng bài hát')
    async def commands_stop(self, ctx):
        """Stop the current song"""
        if ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        else:
            await ctx.send(embed=discord.Embed(description="Có đang hát đéo đâu mà stop?"))

    async def __construct_player_embed(self, ctx, song=None):
        embed = discord.Embed(title="🎵 Player", color=discord.Color.blue())
        
        if song:
            metadata = song.data
        elif ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
            current_source = ctx.voice_client.source
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
        
        guild_id = ctx.message.guild.id
        if guild_id in self.playback_start_time:
            base_elapsed = time.time() - self.playback_start_time[guild_id]
            total_paused = self.total_paused_time.get(guild_id, 0)
            
            if ctx.voice_client and ctx.voice_client.is_paused() and guild_id in self.pause_start_time:
                current_pause_duration = time.time() - self.pause_start_time[guild_id]
                total_paused += current_pause_duration
            
            elapsed = int(base_elapsed - total_paused)
        else:
            elapsed = 0

        if elapsed > total_seconds:
            elapsed = total_seconds

        elapsed_str = format_duration(elapsed) if elapsed >= 0 else "00:00"
        progress_bar = create_progress_bar(elapsed, total_seconds)
        
        status_emoji = "⏸️" if (ctx.voice_client and ctx.voice_client.is_paused()) else "▶️"
        
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
                ctx = message_data['context']
                message = message_data['message']
                
                if not ctx.voice_client or (not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused()):
                    if guild_id in self.player_messages:
                        del self.player_messages[guild_id]
                    continue

                embed = await self.__construct_player_embed(ctx)
                view = PlayerView(self, ctx)
                
                for item in view.children:
                    if isinstance(item, discord.ui.Button) and item.emoji in ['⏸️', '▶️']:
                        if ctx.voice_client.is_paused():
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

    @commands.command(name='player', help='Hiển thị player với progress và danh sách chờ')
    async def commands_player(self, ctx):
        if not ctx.voice_client or (not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused()):
            await ctx.send(embed=discord.Embed(description="Không có bài hát nào đang phát"))
            return

        embed = await self.__construct_player_embed(ctx)
        view = PlayerView(self, ctx)
        
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.emoji in ['⏸️', '▶️']:
                if ctx.voice_client.is_paused():
                    item.emoji = '▶️'
                else:
                    item.emoji = '⏸️'

        message = await ctx.send(embed=embed, view=view)
        self.player_messages[ctx.message.guild.id] = {
            'message': message,
            'context': ctx
        }

    @commands.command(name='playlist', help='Xem playlist cá nhân')
    async def commands_playlist(self, ctx):
        """View user's personal playlist"""
        if not self.db.pool:
            await ctx.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
            return
        
        user_id = ctx.message.author.id
        playlist = await self.db.get_playlist(user_id)
        
        if not playlist:
            await ctx.send(embed=discord.Embed(description="Playlist của bạn trống"))
            return
        
        embed = discord.Embed(title="📋 Playlist cá nhân")
        tracks_list = "\n".join([f"{i+1}. {item.get('title', 'Unknown')} - {item.get('url', '')}" for i, item in enumerate(playlist)])
        embed.description = tracks_list
        await ctx.send(embed=embed)

    @commands.command(name='add', help='Thêm bài hát vào playlist')
    async def commands_add(self, ctx, *, url):
        """Add a song to user's personal playlist"""
        if not self.db.pool:
            await ctx.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
            return
        
        if not url:
            await ctx.send(embed=discord.Embed(description="Cần cung cấp URL hoặc tên bài hát"))
            return
        
        user_id = ctx.message.author.id
        validated_url = validate_url(url)
        
        try:
            songs = await YoutubeDLAudioSource.from_url(validated_url, loop=self.bot.loop, stream=False)
            if not songs:
                await ctx.send(embed=discord.Embed(description="Không tìm thấy bài hát"))
                return
            
            song_title = songs[0].data.get('title', 'Unknown') if songs else 'Unknown'
            if len(songs) > 1:
                song_title = f"{song_title} (và {len(songs) - 1} bài khác)"
            
            success = await self.db.add_song(user_id, validated_url, song_title)
            
            if success:
                if len(songs) == 1:
                    await ctx.send(embed=discord.Embed(description=f"Đã thêm **{songs[0].data['title']}** vào playlist"))
                else:
                    await ctx.send(embed=discord.Embed(description=f"Đã thêm playlist ({len(songs)} bài hát) vào playlist cá nhân"))
            else:
                await ctx.send(embed=discord.Embed(description="Bài hát đã có trong playlist"))
        except Exception as e:
            logger.error(f"Error adding song to playlist: {e}")
            await ctx.send(embed=discord.Embed(description="Lỗi khi thêm bài hát vào playlist"))

    @commands.command(name='remove', help='Xóa bài hát khỏi playlist')
    async def commands_remove(self, ctx, *, identifier):
        """Remove a song from user's personal playlist"""
        if not self.db.pool:
            await ctx.send(embed=discord.Embed(description="Database không khả dụng. Vui lòng thử lại sau."))
            return
        
        if not identifier:
            await ctx.send(embed=discord.Embed(description="Cần cung cấp số thứ tự, URL hoặc tên bài hát"))
            return
        
        user_id = ctx.message.author.id
        success = await self.db.remove_song(user_id, identifier)
        
        if success:
            await ctx.send(embed=discord.Embed(description="Đã xóa bài hát khỏi playlist"))
        else:
            await ctx.send(embed=discord.Embed(description="Không tìm thấy bài hát trong playlist"))

    @commands.command(name='play-playlist', help='Phát playlist cá nhân')
    async def commands_play_playlist(self, ctx):
        """Play user's personal playlist"""
        await self.commands_play(ctx, url="personal")
