from collections import defaultdict
from typing import Dict, Optional
import discord

class GuildState:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: list[discord.FFmpegPCMAudio] = []
        self.current_menu: Optional[discord.ui.View] = None
        self.playback_start_time: Optional[float] = None
        self.pause_start_time: Optional[float] = None
        self.total_paused_time: float = 0.0
        self.player_message: Optional[discord.Message] = None
        self.player_interaction: Optional[discord.Interaction] = None
        self.idle_start_time: Optional[float] = None

class MusicState:
    def __init__(self):
        self._states: Dict[int, GuildState] = {}
    
    def get_guild_state(self, guild_id: int) -> GuildState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildState(guild_id)
        return self._states[guild_id]
    
    def get_queue(self, guild_id: int) -> list:
        return self.get_guild_state(guild_id).queue
    
    def get_playback_start_time(self, guild_id: int) -> Optional[float]:
        return self.get_guild_state(guild_id).playback_start_time
    
    def set_playback_start_time(self, guild_id: int, time: Optional[float]):
        self.get_guild_state(guild_id).playback_start_time = time
    
    def get_pause_start_time(self, guild_id: int) -> Optional[float]:
        return self.get_guild_state(guild_id).pause_start_time
    
    def set_pause_start_time(self, guild_id: int, time: Optional[float]):
        self.get_guild_state(guild_id).pause_start_time = time
    
    def get_total_paused_time(self, guild_id: int) -> float:
        return self.get_guild_state(guild_id).total_paused_time
    
    def set_total_paused_time(self, guild_id: int, time: float):
        self.get_guild_state(guild_id).total_paused_time = time
    
    def get_player_message(self, guild_id: int) -> Optional[discord.Message]:
        return self.get_guild_state(guild_id).player_message
    
    def set_player_message(self, guild_id: int, message: Optional[discord.Message], interaction: Optional[discord.Interaction] = None):
        state = self.get_guild_state(guild_id)
        state.player_message = message
        if interaction:
            state.player_interaction = interaction
    
    def get_player_interaction(self, guild_id: int) -> Optional[discord.Interaction]:
        return self.get_guild_state(guild_id).player_interaction
    
    def get_idle_start_time(self, guild_id: int) -> Optional[float]:
        return self.get_guild_state(guild_id).idle_start_time
    
    def set_idle_start_time(self, guild_id: int, time: Optional[float]):
        self.get_guild_state(guild_id).idle_start_time = time
    
    def clear_idle_start_time(self, guild_id: int):
        self.get_guild_state(guild_id).idle_start_time = None
    
    def clear_player_message(self, guild_id: int):
        state = self.get_guild_state(guild_id)
        state.player_message = None
        state.player_interaction = None
    
    def clear_queue(self, guild_id: int):
        self.get_guild_state(guild_id).queue = []
    
    def remove_guild_state(self, guild_id: int):
        if guild_id in self._states:
            del self._states[guild_id]

global_state = MusicState()

