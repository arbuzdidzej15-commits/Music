import os
import glob
import yt_dlp
from typing import Optional

YDL_SEARCH_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "default_search": "ytsearch10",
}

def search_tracks(query: str) -> list[dict]:
    with yt_dlp.YoutubeDL(YDL_SEARCH_OPTS) as ydl:
        result = ydl.extract_info(f"ytsearch10:{query}", download=False)
        tracks = []
        for entry in (result.get("entries") or []):
            if not entry:
                continue
            tracks.append({
                "id": entry.get("id"),
                "url": f"https://youtube.com/watch?v={entry.get('id')}",
                "title": entry.get("title", "Unknown"),
                "duration": entry.get("duration"),
                "thumbnail": entry.get("thumbnail"),
                "uploader": entry.get("uploader", ""),
                "source": "youtube",
            })
        return tracks

def search_soundcloud(query: str) -> list[dict]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(f"scsearch5:{query}", download=False)
            tracks = []
            for entry in (result.get("entries") or []):
                if not entry:
                    continue
                tracks.append({
                    "id": entry.get("id", ""),
                    "url": entry.get("webpage_url") or entry.get("url", ""),
                    "title": entry.get("title", "Unknown"),
                    "duration": entry.get("duration"),
                    "thumbnail": entry.get("thumbnail"),
                    "uploader": entry.get("uploader", ""),
                    "source": "soundcloud",
                })
            return tracks
    except Exception:
        return []

def get_audio_info(video_url: str) -> Optional[dict]:
    """Returns dict with url, ext. Tries m4a first for browser compatibility."""
    formats_to_try = [
        "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "bestaudio/best",
        "best",
    ]

    for fmt in formats_to_try:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "format": fmt,
            "noplaylist": True,
            "ignoreerrors": True,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                if not info:
                    continue

                url = info.get("url")
                ext = info.get("ext", "webm")
                if url:
                    return {"url": url, "ext": ext, "title": info.get("title", "")}

                formats = info.get("formats", [])
                # prefer pure audio (no video track)
                audio_formats = [
                    f for f in formats
                    if f.get("url") and f.get("acodec") != "none" and f.get("vcodec") == "none"
                ]
                if audio_formats:
                    best = audio_formats[-1]
                    return {"url": best["url"], "ext": best.get("ext", "webm"), "title": info.get("title", "")}

                for f in reversed(formats):
                    if f.get("url"):
                        return {"url": f["url"], "ext": f.get("ext", "webm"), "title": info.get("title", "")}

        except Exception:
            continue

    return None

def get_audio_url(video_url: str) -> Optional[str]:
    info = get_audio_info(video_url)
    return info["url"] if info else None

def download_audio(video_url: str, output_dir: str) -> Optional[str]:
    """Downloads audio to output_dir and returns the file path."""
    outtmpl = os.path.join(output_dir, "audio.%(ext)s")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "ignoreerrors": False,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            if not info:
                return None
            # Find downloaded file (extension may vary)
            files = glob.glob(os.path.join(output_dir, "audio.*"))
            if files:
                return files[0]
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                return filename
    except Exception:
        pass
    return None
