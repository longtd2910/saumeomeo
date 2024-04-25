import logging
import asyncio
from collections import defaultdict

import discord
from discord.ext import commands

from .audio import YoutubeDLAudioSource
from .utils import construct_log

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


class MusicBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue_dict: defaultdict[list[discord.FFmpegPCMAudio]] = {}
        self.current_menu_dict: defaultdict = {}

    @commands.Cog.listener()
    async def on_ready(self):
        logger.debug(construct_log(f'{self.bot.user} has connected to Discord!'))

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

        songs = await YoutubeDLAudioSource.from_url(link, loop=self.bot.loop, stream=False)
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
        else:
            await ctx.send(embed=discord.Embed(description="Có đang hát đéo đâu mà pause?"))

    @commands.command(name='resume', help='Tiếp tục bài hát')
    async def commands_resume(self, ctx):
        """Resume the current song"""
        if ctx.voice_client.is_paused():
            ctx.voice_client.resume()
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
