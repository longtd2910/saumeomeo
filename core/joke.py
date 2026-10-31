import asyncio
import datetime
import logging
import random

import discord

logger = logging.getLogger(__name__)

JOKE_GUILD_ID = 1297350830680444930
JOKE_SOURCE_CHANNEL_ID = 1354752307056869392
JOKE_ANNOUNCE_CHANNEL_ID = 1467287087148699883
VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
JOKE_TIMES = [
    datetime.time(13, 0, tzinfo=VN_TZ),
    datetime.time(20, 0, tzinfo=VN_TZ),
]


class JokeDutyError(Exception):
    pass


def eligible_members(members):
    return [
        member for member in members
        if not getattr(member, "bot", False) and not getattr(member, "pending", False)
    ]


def choose_member(members, last_user_id, rng=None):
    rng = rng or random
    pool = eligible_members(members)
    if not pool:
        return None
    fresh = [member for member in pool if member.id != last_user_id]
    return rng.choice(fresh or pool)


def joke_card(member: discord.Member) -> discord.ui.LayoutView:
    drawn = datetime.datetime.now(VN_TZ).strftime("%H:%M · %d/%m")
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("## 😼 Đến lượt gửi joke"),
            discord.ui.Separator(),
            discord.ui.Section(
                discord.ui.TextDisplay(f"Hôm nay người cần send joke là {member.mention}."),
                discord.ui.TextDisplay(
                    "Trước **12 giờ trưa**, hãy send joke nếu không sẽ bị **khóa mõm 1 ngày**."
                ),
                accessory=discord.ui.Thumbnail(
                    member.display_avatar.replace(size=256).url,
                    description=member.display_name,
                ),
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(f"-# {drawn}  ·  hạn 12:00"),
            accent_colour=discord.Colour.from_rgb(232, 168, 56),
        )
    )
    return view


class JokeDuty:
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self._message_id = None
        self._user_id = None
        self._loaded = False
        self._lock = asyncio.Lock()

    async def announce(self) -> discord.Member:
        async with self._lock:
            return await self._announce()

    async def _announce(self) -> discord.Member:
        guild = self.bot.get_guild(JOKE_GUILD_ID)
        if guild is None:
            raise JokeDutyError("Bot không ở trong server joke.")
        if not guild.chunked:
            try:
                await guild.chunk()
            except discord.DiscordException:
                logger.warning("Could not chunk joke guild members")

        source = await self._channel(guild, JOKE_SOURCE_CHANNEL_ID)
        announce = await self._channel(guild, JOKE_ANNOUNCE_CHANNEL_ID)
        viewers = [
            member for member in guild.members
            if source.permissions_for(member).view_channel
        ]
        await self._load_last()
        member = choose_member(viewers, self._user_id)
        if member is None:
            raise JokeDutyError("Không có ai trong kênh đủ điều kiện gửi joke.")

        logger.info("Joke pool %s viewers, picked %s", len(viewers), member.id)

        message = await announce.send(
            view=joke_card(member),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        previous_id = self._message_id
        self._message_id = message.id
        self._user_id = member.id
        if self.db.pool:
            await self.db.save_joke_announcement(
                JOKE_GUILD_ID, announce.id, message.id, member.id
            )
        if previous_id and previous_id != message.id:
            await self._delete_message(announce, previous_id)
        return member

    async def _channel(self, guild, channel_id):
        channel = guild.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        if getattr(channel, "guild", None) is None or channel.guild.id != guild.id:
            raise JokeDutyError("Kênh joke không thuộc server này.")
        return channel

    async def _load_last(self):
        if self._loaded or not self.db.pool:
            return
        self._loaded = True
        row = await self.db.get_joke_announcement(JOKE_GUILD_ID)
        if not row:
            return
        self._message_id = row.get("message_id")
        self._user_id = row.get("user_id")

    async def _delete_message(self, channel, message_id):
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except discord.NotFound:
            return
        except discord.DiscordException as error:
            logger.warning("Could not delete previous joke announcement: %s", error)
