import asyncio
import json
import os
import shutil
import tempfile
from urllib.parse import urlparse

import discord

from .utils import format_duration

ffmpeg_file_options = {
    'options': '-vn'
}

YOUTUBE_STRATEGIES = [
    ('bestaudio[protocol^=m3u8]/bestaudio/best', 'youtube:player_client=android,web'),
    ('bestaudio/best', 'youtube:player_client=android,web'),
    ('bestaudio[protocol^=m3u8]/bestaudio/best', 'youtube:player_client=web'),
    ('bestaudio/best', 'youtube:player_client=web'),
    ('bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best', 'youtube:player_client=android,web'),
    ('bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best', 'youtube:player_client=web,mweb'),
    ('bestaudio/best', 'youtube:player_client=mweb,web'),
]

DEFAULT_STRATEGIES = [
    ('bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best', None),
]


def cleanup_source(source: discord.AudioSource) -> None:
    try:
        source.cleanup()
    except Exception:
        pass


class _TempFFmpegPCMAudio(discord.FFmpegPCMAudio):
    def __init__(self, source: str, *, temp_dir: str, **kwargs):
        self._temp_dir = temp_dir
        super().__init__(source, **kwargs)

    def cleanup(self) -> None:
        super().cleanup()
        shutil.rmtree(self._temp_dir, ignore_errors=True)


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
    def _resolve_entry_download_url(cls, entry, fallback_url):
        u = entry.get('webpage_url') or entry.get('original_url')
        if not u or u.startswith('ytsearch:'):
            u = entry.get('webpage_url') or fallback_url
        if not u and entry.get('id'):
            vid = entry['id']
            if isinstance(vid, str) and len(vid) == 11:
                u = f'https://www.youtube.com/watch?v={vid}'
        return u

    @classmethod
    def _youtube_opts(cls, extractor: str | None, use_impersonate: bool) -> list[str]:
        opts = [
            '--remote-components', 'ejs:npm',
            '--force-ipv4',
            '--extractor-retries', '3',
            '--fragment-retries', '10',
        ]
        if use_impersonate:
            opts.extend(['--impersonate', 'chrome'])
        if extractor:
            opts.extend(['--extractor-args', extractor])
        return opts

    @classmethod
    async def _download_track(
        cls,
        entry_url: str,
        format_selector: str,
        youtube_extractor: str | None,
        use_impersonate: bool,
    ) -> tuple[str, str]:
        tmp_dir = tempfile.mkdtemp(prefix='saumeomeo_')
        out_tmpl = os.path.join(tmp_dir, 'audio.%(ext)s')
        cmd = ['yt-dlp']
        if cls._is_youtube_url(entry_url):
            cmd.extend(cls._youtube_opts(youtube_extractor, use_impersonate))
        cmd.extend([
            '-f', format_selector,
            '-o', out_tmpl,
            '--no-playlist',
            '--no-warnings',
            '--no-write-info-json',
            '--no-write-thumbnail',
            '--no-embed-thumbnail',
            entry_url,
        ])
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise RuntimeError(stderr.decode() if stderr else 'yt-dlp download failed')
        names = [f for f in os.listdir(tmp_dir) if os.path.isfile(os.path.join(tmp_dir, f))]
        if not names:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise RuntimeError('yt-dlp produced no file')
        path = os.path.join(tmp_dir, names[0])
        return path, tmp_dir

    @classmethod
    async def from_url(self, url, *, loop=None, stream=False, n=None):
        is_playlist = 'playlist' in url.lower() or 'list=' in url.lower()

        if n is None:
            if is_playlist:
                n = 10
            else:
                n = 1

        limit = n

        if self._is_youtube_url(url):
            strategy_loops = [
                (True, YOUTUBE_STRATEGIES),
                (False, YOUTUBE_STRATEGIES),
            ]
        else:
            strategy_loops = [(False, DEFAULT_STRATEGIES)]

        last_error = None
        for use_impersonate, strategies in strategy_loops:
            for format_selector, youtube_extractor in strategies:
                cmd = ['yt-dlp']
                if self._is_youtube_url(url):
                    cmd.extend(self._youtube_opts(youtube_extractor, use_impersonate))
                cmd.extend([
                    '--dump-single-json',
                    '--playlist-end',
                    str(limit),
                    '--no-warnings',
                    '-f',
                    format_selector,
                    url,
                ])

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()

                if process.returncode != 0:
                    last_error = stderr.decode() if stderr else 'yt-dlp failed'
                    continue

                try:
                    data = json.loads(stdout.decode())
                    entries = data['entries'] if 'entries' in data else [data]
                    entries = entries[:limit]
                    results = []
                    download_failed = False
                    for entry in entries:
                        entry_url = self._resolve_entry_download_url(entry, url)
                        if not entry_url:
                            continue
                        try:
                            path, tmp_dir = await self._download_track(
                                entry_url,
                                format_selector,
                                youtube_extractor,
                                use_impersonate,
                            )
                        except RuntimeError as e:
                            last_error = str(e)
                            for r in results:
                                cleanup_source(r)
                            results = []
                            download_failed = True
                            break
                        entry_url_display = entry.get('webpage_url') or entry.get('original_url') or url
                        if not entry_url_display or entry_url_display.startswith('ytsearch:'):
                            entry_url_display = entry.get('webpage_url') or url
                        audio_source = self(
                            _TempFFmpegPCMAudio(path, temp_dir=tmp_dir, **ffmpeg_file_options),
                            data={
                                'title': entry.get('title', 'No title'),
                                'duration': format_duration(entry.get('duration', 0)),
                                'url': entry_url_display,
                            },
                        )
                        audio_source.url = entry_url_display
                        results.append(audio_source)
                    if download_failed:
                        continue
                    if results:
                        return results
                except (json.JSONDecodeError, KeyError) as e:
                    last_error = f'Failed to parse yt-dlp output: {e}'
                    continue

        raise RuntimeError(last_error or 'yt-dlp failed with all format selectors')
