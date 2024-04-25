from datetime import datetime

def construct_log(log):
    """Append datetime into log message"""
    return f"{datetime.now()} | {log}"

def validate_url(url):
    """Check if the URL from Youtube or Soundcloud"""

def format_duration(duration: int):
    """Format the duration of the media from length to MM:SS or HH:MM:SS"""
    if duration < 3600:
        return "{:02d}:{:02d}".format(duration // 60, duration % 60)
    else:
        return "{:02d}:{:02d}:{:02d}".format(duration // 3600, (duration % 3600) // 60, duration % 60)
    