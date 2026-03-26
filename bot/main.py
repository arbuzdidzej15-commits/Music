import asyncio
import os
import sys
import tempfile
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
from search import search_tracks, search_soundcloud, download_audio

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, FSInputFile

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8743751007:AAE216Uep_ttyslYecsJIjoPIRu1WM6QThc")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

user_results: dict[int, list[dict]] = {}

# Хранилище: key -> {track, file_id, chain}
# file_id — Telegram file_id последней версии аудио
# chain   — список применённых эффектов (для отображения)
_store: dict[str, dict] = {}
_counter = 0

def new_key() -> str:
    global _counter
    _counter += 1
    return str(_counter)

# ── Эффекты (русские названия) ────────────────────────────────────────────────
EFFECTS: list[tuple[str, str, str | None]] = [
    ("bass",      "🔊 Бас-буст",            "bass=g=12:f=200"),
    ("treble",    "✨ Усиление высоких",     "treble=g=8:f=4000"),
    ("speedup",   "⚡ Ускорение ×1.5",      "atempo=1.5"),
    ("slowdown",  "🐌 Замедление ×0.75",    "atempo=0.75"),
    ("nightcore", "🌙 Найткор",             "atempo=1.25,bass=g=8:f=200,treble=g=4:f=4000"),
    ("vaporwave", "🌊 Вейпорвейв",          "atempo=0.8,aecho=0.8:0.88:80:0.5"),
    ("reverb",    "🏛 Реверб",              "aecho=0.9:0.9:100:0.3,aecho=0.9:0.9:200:0.2"),
]

def effect_label(key: str) -> str:
    for k, label, _ in EFFECTS:
        if k == key:
            return label
    return key

def effect_af(key: str) -> str | None:
    for k, _, af in EFFECTS:
        if k == key:
            return af
    return None

def kb_main(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎛 Эффекты", callback_data=f"eff:{key}")
    ]])

def kb_effects(key: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"efx:{key}:{k}")]
            for k, label, _ in EFFECTS]
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data=f"efc:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def fmt_dur(s) -> str:
    if not s or not isinstance(s, (int, float)): return "?:??"
    s = int(s)
    if s >= 3600: return f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}"
    return f"{s//60}:{s%60:02d}"

# ── ffmpeg ────────────────────────────────────────────────────────────────────
def run_ffmpeg(src: str, dst: str, af: str) -> bool:
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-af", af,
             "-codec:a", "libmp3lame", "-q:a", "2", dst],
            capture_output=True, timeout=180,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

# ── Скачать файл из Telegram ──────────────────────────────────────────────────
async def download_tg_file(file_id: str, dest: str) -> bool:
    try:
        await bot.download(file_id, destination=dest)
        return os.path.exists(dest) and os.path.getsize(dest) > 0
    except Exception:
        return False

# ── Отправить аудио ───────────────────────────────────────────────────────────
async def send_audio(
    reply_to: types.Message,
    status: types.Message,
    file_path: str,
    track: dict,
    chain: list[str],
) -> str | None:
    """Отправляет аудио, возвращает Telegram file_id."""
    title = track["title"]
    dur = int(track.get("duration") or 0)
    src_label = "YouTube" if track["source"] == "youtube" else "SoundCloud"

    if not chain:
        fx_line = ""
    else:
        fx_line = "\n🎛 " + " → ".join(effect_label(k) for k in chain)

    caption = f"🎵 <b>{title}</b>{fx_line}\n⏱ {fmt_dur(dur)} · {src_label}"

    key = new_key()
    await status.edit_text("📤 Отправляю...")

    msg = await reply_to.answer_audio(
        audio=FSInputFile(file_path, filename=f"{title[:50]}.mp3"),
        title=title,
        duration=dur or None,
        caption=caption,
        parse_mode="HTML",
        reply_markup=kb_main(key),
    )

    # Сохраняем file_id нового сообщения
    sent_file_id = msg.audio.file_id
    _store[key] = {"track": track, "file_id": sent_file_id, "chain": chain}
    return key

# ── /start ────────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎵 Открыть плеер", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await message.answer(
        "👋 Привет! Я музыкальный бот.\n\n"
        "🔍 <b>Напиши название трека</b> — найду на YouTube и SoundCloud.\n"
        "🎛 Под каждым треком — кнопка <b>Эффекты</b>.\n"
        "Эффекты можно накладывать бесконечно друг на друга.\n\n"
        "📱 Или открой плеер:",
        parse_mode="HTML",
        reply_markup=kb,
    )

# ── Поиск ─────────────────────────────────────────────────────────────────────
@dp.message(F.text)
async def handle_search(message: types.Message):
    query = message.text.strip()
    if not query: return

    status = await message.answer("🔍 Ищу на YouTube и SoundCloud...")
    try:
        loop = asyncio.get_event_loop()
        yt, sc = await asyncio.gather(
            loop.run_in_executor(None, search_tracks, query),
            loop.run_in_executor(None, search_soundcloud, query),
        )
        results = yt[:5] + sc[:5]
        if not results:
            await status.edit_text("😔 Ничего не найдено."); return

        user_results[message.from_user.id] = results
        buttons = []
        for i, t in enumerate(results):
            icon = "▶️" if t["source"] == "youtube" else "☁️"
            title = t["title"][:40] + ("…" if len(t["title"]) > 40 else "")
            buttons.append([InlineKeyboardButton(
                text=f"{icon} {title} [{fmt_dur(t.get('duration'))}]",
                callback_data=f"dl:{i}",
            )])
        await status.edit_text(
            f"🎵 Найдено <b>{len(results)}</b> треков:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception as e:
        await status.edit_text(f"⚠️ Ошибка: {e}")

# ── Трек из поиска → скачать чистый ──────────────────────────────────────────
@dp.callback_query(F.data.startswith("dl:"))
async def handle_dl(callback: types.CallbackQuery):
    idx = int(callback.data.split(":")[1])
    tracks = user_results.get(callback.from_user.id, [])
    if idx >= len(tracks):
        await callback.answer("Сделай новый поиск.", show_alert=True); return

    track = tracks[idx]
    await callback.answer()
    status = await callback.message.answer(f"⏳ Скачиваю «{track['title'][:40]}»...")

    video_url = (f"https://youtube.com/watch?v={track['id']}"
                 if track["source"] == "youtube" else track.get("url", ""))
    if not video_url:
        await status.edit_text("❌ Нет ссылки."); return

    loop = asyncio.get_event_loop()
    with tempfile.TemporaryDirectory() as tmpdir:
        path = await loop.run_in_executor(None, download_audio, video_url, tmpdir)
        if not path or not os.path.exists(path):
            await status.edit_text("❌ Не удалось скачать."); return
        if os.path.getsize(path) > 50 * 1024 * 1024:
            await status.edit_text("❌ Файл > 50 МБ."); return

        await send_audio(callback.message, status, path, track, chain=[])

# ── Открыть меню эффектов ─────────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("eff:"))
async def handle_eff(callback: types.CallbackQuery):
    key = callback.data.split(":", 1)[1]
    if key not in _store:
        await callback.answer("Трек устарел — найди заново.", show_alert=True); return
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=kb_effects(key))

# ── Закрыть меню эффектов ─────────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("efc:"))
async def handle_efc(callback: types.CallbackQuery):
    key = callback.data.split(":", 1)[1]
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=kb_main(key))

# ── Применить эффект поверх текущего файла ────────────────────────────────────
@dp.callback_query(F.data.startswith("efx:"))
async def handle_efx(callback: types.CallbackQuery):
    _, key, effect_key = callback.data.split(":", 2)
    if key not in _store:
        await callback.answer("Трек устарел — найди заново.", show_alert=True); return

    entry = _store[key]
    track = entry["track"]
    file_id = entry["file_id"]
    chain = entry["chain"]
    af = effect_af(effect_key)

    await callback.answer(f"⏳ {effect_label(effect_key)}")
    # Восстанавливаем кнопку на исходном сообщении
    await callback.message.edit_reply_markup(reply_markup=kb_main(key))

    label = effect_label(effect_key)
    status = await callback.message.answer(f"⏳ Накладываю {label}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "source.mp3")

        # Скачиваем ТЕКУЩИЙ файл из Telegram (уже обработанный)
        ok = await download_tg_file(file_id, src)
        if not ok:
            await status.edit_text("❌ Не удалось скачать файл из Telegram."); return

        if af:
            await status.edit_text(f"🎛 Применяю {label}...")
            dst = os.path.join(tmpdir, "out.mp3")
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(None, run_ffmpeg, src, dst, af)
            if success and os.path.exists(dst):
                send_path = dst
                new_chain = chain + [effect_key]
            else:
                await status.edit_text("⚠️ ffmpeg не справился, отправляю без изменений.")
                await asyncio.sleep(1)
                send_path = src
                new_chain = chain
        else:
            send_path = src
            new_chain = chain

        if os.path.getsize(send_path) > 50 * 1024 * 1024:
            await status.edit_text("❌ Файл > 50 МБ."); return

        await send_audio(callback.message, status, send_path, track, chain=new_chain)

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print("Bot started.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
