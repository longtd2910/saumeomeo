import logging
import asyncio
import time
from collections import defaultdict

import discord
from discord.ext import commands, tasks

from .audio import YoutubeDLAudioSource
from .utils import construct_log, validate_url, parse_duration, create_progress_bar, format_duration

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
        self.update_player_task.start()

    @commands.Cog.listener()
    async def on_ready(self):
        logger.debug(construct_log(f'{self.bot.user} has connected to Discord!'))

    def cog_unload(self):
        self.update_player_task.cancel()

    async def join(self, ctx):
        if not ctx.message.author.voice:
            await ctx.send(embed=discord.Embed(description="Không ở trong kênh thì vào hát kiểu lz gì?"))
            return
        
        if ctx.voice_client is not None and ctx.voice_client.channel != ctx.message.author.voice.channel:
            await ctx.send(embed=discord.Embed(description="Tao đang hát ở chỗ khác rồi"))

        if ctx.voice_client is None:
            await ctx.message.author.voice.channel.connect()

    async def __resolve_link(self, voice_id, link):
        """Classify a link. Return a discord.PCMVolumeTransformer object"""
        if voice_id not in self.queue_dict:
            self.queue_dict[voice_id] = []

        songs = await YoutubeDLAudioSource.from_url(validate_url(link), loop=self.bot.loop, stream=False)
        self.queue_dict[voice_id] += songs
        return len(songs)

    async def __play_next(self, ctx):
        if len(self.queue_dict[ctx.message.guild.id]) == 0:
            return
        
        song = self.queue_dict[ctx.message.guild.id].pop(0)

        view, embed = await self.__construct_media_buttons(ctx, song.data)

        await ctx.send(
            embed = embed,
            view=view
        )

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

        guild_id = ctx.message.guild.id
        self.playback_start_time[guild_id] = time.time()
        self.total_paused_time[guild_id] = 0
        if guild_id in self.pause_start_time:
            del self.pause_start_time[guild_id]
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
        songs = await self.__resolve_link(ctx.message.guild.id, url)
        
        if len(self.queue_dict[server_id]) - songs + 1 if ctx.voice_client.is_playing() else 0 > 0:
            if songs == 1:
                await ctx.send(embed=discord.Embed(description=f"Đã thêm **{self.queue_dict[server_id][-1].data['title']}**"))
            else:
                await ctx.send(embed=discord.Embed(description=f"Đã thêm {songs} bài hát vào hàng chờ"))
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

    async def __construct_player_embed(self, ctx):
        embed = discord.Embed(title="🎵 Player", color=discord.Color.blue())
        
        if not ctx.voice_client or not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
            embed.description = "Không có bài hát nào đang phát"
            return embed

        current_source = ctx.voice_client.source
        if not hasattr(current_source, 'data'):
            embed.description = "Không thể lấy thông tin bài hát"
            return embed

        metadata = current_source.data
        title = metadata.get('title', 'Unknown')
        duration_str = metadata.get('duration', '00:00')
        total_seconds = parse_duration(duration_str)
        
        guild_id = ctx.message.guild.id
        if guild_id in self.playback_start_time:
            base_elapsed = time.time() - self.playback_start_time[guild_id]
            total_paused = self.total_paused_time.get(guild_id, 0)
            
            if ctx.voice_client.is_paused() and guild_id in self.pause_start_time:
                current_pause_duration = time.time() - self.pause_start_time[guild_id]
                total_paused += current_pause_duration
            
            elapsed = int(base_elapsed - total_paused)
        else:
            elapsed = 0

        if elapsed > total_seconds:
            elapsed = total_seconds

        elapsed_str = format_duration(elapsed) if elapsed >= 0 else "00:00"
        progress_bar = create_progress_bar(elapsed, total_seconds)
        
        status_emoji = "⏸️" if ctx.voice_client.is_paused() else "▶️"
        
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
