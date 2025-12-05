import asyncio
import json

import discord

from .utils import format_duration

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx',
    'options': '-vn'
}

class YoutubeDLAudioSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = ""

    @classmethod
    async def from_url(self, url, *, loop=None, stream=False):
        command = [
            "yt-dlp",
            "--cookies",
            "cookies.txt",
            "--dump-single-json",
            "--no-playlist",
            "--no-warnings",
            "-f",
            "bestaudio/best",
            url
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode() or "yt-dlp failed")
        data = json.loads(stdout.decode())
        entries = data['entries'] if 'entries' in data else [data]
        results = []
        for entry in entries:
            stream_url = entry.get('url')
            if not stream_url and entry.get('formats'):
                stream_url = entry['formats'][-1].get('url')
            results.append(
                self(
                    discord.FFmpegPCMAudio(
                        stream_url,
                        **ffmpeg_options
                    ),
                    data={
                        'title': entry.get('title', 'No title'),
                        'duration': format_duration(entry.get('duration', 0))
                    }
                )
            )
        return results
