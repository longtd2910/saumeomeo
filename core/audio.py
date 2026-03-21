import asyncio
import json
import subprocess
import sys
from urllib.parse import urlparse

import discord

from .utils import format_duration

ffmpeg_pipe_options = {
    'before_options': '-thread_queue_size 512',
    'options': '-vn'
}

def _popen_ytdlp_stdout(cmd: list[str]) -> subprocess.Popen:
    kwargs: dict = {
        'stdout': subprocess.PIPE,
        'stderr': subprocess.DEVNULL,
    }
    if sys.platform == 'win32':
        kwargs['creationflags'] = 0x08000000
    return subprocess.Popen(cmd, **kwargs)

class YtdlpPipeIntoFFmpegPCMAudio(discord.FFmpegPCMAudio):
    def __init__(self, ytdlp_proc: subprocess.Popen, **kwargs):
        self._ytdlp_proc = ytdlp_proc
        super().__init__(ytdlp_proc.stdout, pipe=True, **kwargs)

    def cleanup(self):
        super().cleanup()
        if self._ytdlp_proc.poll() is None:
            self._ytdlp_proc.kill()
        try:
            self._ytdlp_proc.wait(timeout=3)
        except Exception:
            pass

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
    def _playback_url(cls, entry, query_url):
        w = entry.get('webpage_url') or entry.get('original_url')
        if w and not str(w).startswith('ytsearch:'):
            return w
        u = entry.get('url')
        if u and ('youtube.com' in u or 'youtu.be' in u):
            return u
        vid = entry.get('id')
        if isinstance(vid, str) and len(vid) == 11:
            return f'https://www.youtube.com/watch?v={vid}'
        return query_url

    @classmethod
    async def from_url(self, url, *, loop=None, stream=False, n=None):
        command = ["yt-dlp"]
        
        if self._is_youtube_url(url):
            command.extend(["--remote-components", "ejs:npm"])
        
        format_selectors = [
            "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        ]
        
        is_search = url.startswith('ytsearch')
        is_playlist = 'playlist' in url.lower() or 'list=' in url.lower()
        
        if n is None:
            if is_playlist:
                n = 10
            else:
                n = 1
        
        limit = n
        
        last_error = None
        for format_selector in format_selectors:
            cmd = command + [
                "--dump-single-json",
                "--playlist-end",
                str(limit),
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
                    entries = entries[:limit]
                    results = []
                    for entry in entries:
                        play_url = self._playback_url(entry, url)
                        if not play_url:
                            continue
                        dl_cmd = ["yt-dlp"]
                        if self._is_youtube_url(play_url):
                            dl_cmd.extend(["--remote-components", "ejs:npm"])
                        dl_cmd.extend([
                            "-f", format_selector,
                            "-o", "-",
                            "--no-warnings",
                            "--no-playlist",
                            play_url,
                        ])
                        ytdlp_proc = await asyncio.to_thread(_popen_ytdlp_stdout, dl_cmd)
                        entry_url = entry.get('webpage_url') or entry.get('original_url') or url
                        if not entry_url or entry_url.startswith('ytsearch:'):
                            entry_url = entry.get('webpage_url') or url
                        audio_source = self(
                            YtdlpPipeIntoFFmpegPCMAudio(
                                ytdlp_proc,
                                **ffmpeg_pipe_options
                            ),
                            data={
                                'title': entry.get('title', 'No title'),
                                'duration': format_duration(entry.get('duration', 0)),
                                'url': entry_url
                            }
                        )
                        audio_source.url = entry_url
                        results.append(audio_source)
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
