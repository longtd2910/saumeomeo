import asyncio
import json
import os
from urllib.parse import urlparse

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
    def _is_youtube_url(cls, url):
        parsed = urlparse(url)
        return 'youtube.com' in parsed.netloc or 'youtu.be' in parsed.netloc

    @classmethod
    async def from_url(self, url, *, loop=None, stream=False):
        command = ["yt-dlp"]
        
        # cookies_path = "cookies/youtube.txt"
        # if self._is_youtube_url(url) and os.path.exists(cookies_path):
        #     command.extend(["--cookies", cookies_path])
        
        if self._is_youtube_url(url):
            command.extend(["--remote-components", "ejs:npm"])
        
        format_selectors = [
            "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "bestaudio/best",
            "best"
        ]
        
        last_error = None
        for format_selector in format_selectors:
            cmd = command + [
                "--dump-single-json",
                "--playlist-end",
                "10",
                "--no-warnings",
                "-f",
                format_selector,
                url
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                try:
                    data = json.loads(stdout.decode())
                    entries = data['entries'] if 'entries' in data else [data]
                    entries = entries[:10]
                    results = []
                    for entry in entries:
                        stream_url = entry.get('url')
                        if not stream_url and entry.get('formats'):
                            for fmt in reversed(entry['formats']):
                                if fmt.get('url'):
                                    stream_url = fmt.get('url')
                                    break
                        if not stream_url:
                            continue
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
                    if results:
                        return results
                except (json.JSONDecodeError, KeyError) as e:
                    last_error = f"Failed to parse yt-dlp output: {e}"
                    continue
            
            error_msg = stderr.decode() if stderr else "yt-dlp failed"
            if "Requested format is not available" not in error_msg:
                last_error = error_msg
                break
            last_error = error_msg
        
        raise RuntimeError(last_error or "yt-dlp failed with all format selectors")
