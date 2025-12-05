from datetime import datetime
from urllib.parse import urlparse

def construct_log(log):
    return f"{datetime.now()} | {log}"

def validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        return url
    return f"ytsearch:{url}"

def format_duration(duration: int):
    if duration < 3600:
        return "{:02d}:{:02d}".format(duration // 60, duration % 60)
    else:
        return "{:02d}:{:02d}:{:02d}".format(duration // 3600, (duration % 3600) // 60, duration % 60)

def parse_duration(duration_str: str) -> int:
    parts = duration_str.split(':')
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0

def create_progress_bar(current: int, total: int, length: int = 20) -> str:
    if total == 0:
        return '▱' * length
    filled = int((current / total) * length)
    bar = '▰' * filled + '▱' * (length - filled)
    return bar
    