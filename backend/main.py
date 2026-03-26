import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
import httpx
import asyncio
from search import search_tracks, get_audio_info

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
)

MIME_MAP = {
    "m4a":  "audio/mp4",
    "mp4":  "audio/mp4",
    "webm": "audio/webm",
    "mp3":  "audio/mpeg",
    "ogg":  "audio/ogg",
    "opus": "audio/ogg; codecs=opus",
}

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/search")
async def search(q: str):
    if not q or len(q) < 2:
        raise HTTPException(400, "Query too short")
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, search_tracks, q)
        return {"results": results}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/stream")
async def stream_audio(video_id: str, request: Request):
    url = f"https://youtube.com/watch?v={video_id}"
    try:
        loop = asyncio.get_event_loop()
        audio_info = await loop.run_in_executor(None, get_audio_info, url)
        if not audio_info or not audio_info.get("url"):
            raise HTTPException(404, "Audio not found")

        direct_url = audio_info["url"]
        ext = audio_info.get("ext", "webm")
        media_type = MIME_MAP.get(ext, "audio/webm")

        upstream_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.youtube.com/",
            "Origin": "https://www.youtube.com",
        }
        # Forward Range header — required for mobile browsers (iOS/Android)
        range_header = request.headers.get("range")
        if range_header:
            upstream_headers["Range"] = range_header

        # Open client outside generator so we can read upstream headers first
        client = httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True)
        try:
            r = await client.send(
                client.build_request("GET", direct_url, headers=upstream_headers),
                stream=True,
            )
        except Exception as e:
            await client.aclose()
            raise HTTPException(502, str(e))

        resp_headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
            "Access-Control-Allow-Origin": "*",
        }
        for h in ("Content-Length", "Content-Range"):
            if h in r.headers:
                resp_headers[h] = r.headers[h]

        async def generator():
            try:
                async for chunk in r.aiter_bytes(65536):
                    yield chunk
            finally:
                await r.aclose()
                await client.aclose()

        return StreamingResponse(
            generator(),
            status_code=r.status_code,
            media_type=media_type,
            headers=resp_headers,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

app.mount("/static", StaticFiles(directory="static"), name="static")
