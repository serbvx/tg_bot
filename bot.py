"""
Telegram-бот для скачивания видео/фото без водяных знаков
(TikTok, YouTube, Instagram, Twitter/X и другие платформы, поддерживаемые yt-dlp).

Запуск локально:
    export BOT_TOKEN=твой_токен
    python bot.py

На Render.com бот работает как Web Service: поднимает лёгкий HTTP-сервер
(для health-check, который требует Render) и параллельно опрашивает Telegram
через long polling в фоновом потоке.
"""

import os
import re
import glob
import base64
import shutil
import asyncio
import logging
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from telegram import Update, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("tg-downloader-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "Не найден BOT_TOKEN. Задай переменную окружения BOT_TOKEN "
        "(на Render — во вкладке Environment)."
    )

# --- Cookies для YouTube (и других площадок, если понадобится) ---------
# YouTube требует авторизацию для запросов с IP облачных серверов.
# Cookies передаются через переменную окружения YT_COOKIES_B64 —
# это содержимое файла cookies.txt, закодированное в base64 (см. README).
COOKIES_PATH = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")

_cookies_b64 = os.environ.get("YT_COOKIES_B64")
if _cookies_b64:
    try:
        with open(COOKIES_PATH, "wb") as _f:
            _f.write(base64.b64decode(_cookies_b64))
    except Exception:
        logging.getLogger("tg-downloader-bot").exception(
            "Не удалось декодировать YT_COOKIES_B64 — cookies не будут использоваться."
        )
        COOKIES_PATH = None
else:
    COOKIES_PATH = None

# Telegram Bot API не даёт ботам отправлять файлы тяжелее ~50 МБ.
MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

URL_REGEX = re.compile(r"https?://[^\s]+")

SUPPORTED_HINT = (
    "Отправь мне ссылку на видео или фото из *TikTok*, *YouTube*, "
    "*Instagram*, *Twitter/X* и т.д.\n\n"
    "Я скачаю контент без водяных знаков и пришлю его сюда."
)


# ---------------------------------------------------------------------------
# Скачивание TikTok через tikwm.com (обходит текущий баг в yt-dlp — см. README)
# ---------------------------------------------------------------------------

def is_tiktok_url(url: str) -> bool:
    return "tiktok.com" in url.lower()


def download_tiktok_via_api(url: str, download_dir: str) -> list[str]:
    """
    Скачивает TikTok-видео/фото без водяного знака через сторонний API
    tikwm.com. Возвращает список путей к скачанным файлам, либо
    поднимает RuntimeError, если API не смог обработать ссылку.
    """
    resp = requests.get(
        "https://www.tikwm.com/api/",
        params={"url": url, "hd": 1},
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    payload = resp.json()

    if payload.get("code") != 0 or "data" not in payload:
        raise RuntimeError(f"tikwm API error: {payload.get('msg', 'unknown error')}")

    data = payload["data"]
    files: list[str] = []

    images = data.get("images")
    if images:
        # Фото-карусель
        for i, img_url in enumerate(images):
            img_resp = requests.get(img_url, timeout=30)
            img_resp.raise_for_status()
            path = os.path.join(download_dir, f"tiktok_{i}.jpg")
            with open(path, "wb") as f:
                f.write(img_resp.content)
            files.append(path)
        return files

    # Видео — hdplay (HD без водяного знака) с фолбэком на play
    video_url = data.get("hdplay") or data.get("play")
    if not video_url:
        raise RuntimeError("tikwm API не вернул ссылку на видео")

    video_resp = requests.get(video_url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    video_resp.raise_for_status()
    path = os.path.join(download_dir, "tiktok.mp4")
    with open(path, "wb") as f:
        f.write(video_resp.content)
    files.append(path)
    return files


# ---------------------------------------------------------------------------
# Скачивание через yt-dlp (YouTube, Instagram, Twitter/X и всё остальное;
# для TikTok используется как запасной вариант, см. download_tiktok_via_api)
# ---------------------------------------------------------------------------

def download_media(url: str, download_dir: str) -> list[str]:
    """
    Скачивает медиа по ссылке в download_dir и возвращает список путей
    к скачанным файлам (может быть видео или набор фото/слайдов).
    """
    outtmpl = os.path.join(download_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }

    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        ydl_opts["cookiefile"] = COOKIES_PATH

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    files = sorted(glob.glob(os.path.join(download_dir, "*")))
    return files


class TikTokDownloadFailed(Exception):
    """Оба способа скачать TikTok не сработали — хранит причины обоих."""

    def __init__(self, tikwm_error: str, ytdlp_error: str):
        self.tikwm_error = tikwm_error
        self.ytdlp_error = ytdlp_error
        super().__init__(f"tikwm: {tikwm_error} | yt-dlp: {ytdlp_error}")


def download_any(url: str, download_dir: str) -> list[str]:
    """
    Единая точка входа для скачивания: для TikTok сперва пробует
    tikwm.com (быстрее и сейчас надёжнее из-за бага в yt-dlp), при
    неудаче откатывается на yt-dlp. Для остального — сразу yt-dlp.
    """
    if is_tiktok_url(url):
        try:
            return download_tiktok_via_api(url, download_dir)
        except Exception as tikwm_exc:
            logging.getLogger("tg-downloader-bot").exception(
                "tikwm API не сработал для %s, пробую через yt-dlp", url
            )
            try:
                return download_media(url, download_dir)
            except Exception as ytdlp_exc:
                raise TikTokDownloadFailed(str(tikwm_exc), str(ytdlp_exc)) from ytdlp_exc
    return download_media(url, download_dir)


def is_photo(path: str) -> bool:
    return path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))


def is_video(path: str) -> bool:
    return path.lower().endswith((".mp4", ".mov", ".mkv", ".webm"))


# ---------------------------------------------------------------------------
# Хендлеры бота
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(SUPPORTED_HINT, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(SUPPORTED_HINT, parse_mode=ParseMode.MARKDOWN)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    match = URL_REGEX.search(text)

    if not match:
        await update.message.reply_text(
            "Пришли, пожалуйста, ссылку на видео или фото (TikTok, YouTube, "
            "Instagram и т.д.)."
        )
        return

    url = match.group(0)
    status_msg = await update.message.reply_text("⏳ Скачиваю, подожди немного...")

    tmp_dir = tempfile.mkdtemp(prefix="tgdl_")
    try:
        loop = asyncio.get_running_loop()
        files = await loop.run_in_executor(None, download_any, url, tmp_dir)

        if not files:
            await status_msg.edit_text(
                "Не получилось ничего скачать по этой ссылке 😕\n"
                "Проверь, что ссылка рабочая и ведёт на публичный пост."
            )
            return

        photos = [f for f in files if is_photo(f)]
        videos = [f for f in files if is_video(f)]

        # Одно видео
        if len(videos) == 1 and not photos:
            path = videos[0]
            size = os.path.getsize(path)
            if size > MAX_TELEGRAM_FILE_SIZE:
                await status_msg.edit_text(
                    "Файл скачался, но он больше 50 МБ — столько Telegram "
                    "не позволяет ботам отправлять. Попробуй ссылку на "
                    "видео покороче или в меньшем качестве."
                )
                return
            with open(path, "rb") as f:
                await update.message.reply_video(video=f, caption="Готово ✅")
            await status_msg.delete()
            return

        # Несколько фото (слайд-шоу/карусель, например из TikTok/Instagram)
        if photos and not videos:
            media_group = [InputMediaPhoto(open(p, "rb")) for p in photos[:10]]
            await update.message.reply_media_group(media=media_group)
            await status_msg.delete()
            return

        # Смешанный случай — на всякий пожарный отправляем всё по отдельности
        for path in files:
            size = os.path.getsize(path)
            if size > MAX_TELEGRAM_FILE_SIZE:
                continue
            with open(path, "rb") as f:
                if is_photo(path):
                    await update.message.reply_photo(photo=f)
                elif is_video(path):
                    await update.message.reply_video(video=f)
        await status_msg.delete()

    except TikTokDownloadFailed as e:
        logger.warning("TikTok: оба способа не сработали: %s", e)
        tikwm_snippet = e.tikwm_error[:300]
        await status_msg.edit_text(
            "Не смог скачать это TikTok-видео ни одним из двух способов 😕\n\n"
            f"Тех. детали tikwm (временно, для отладки):\n`{tikwm_snippet}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except yt_dlp.utils.DownloadError as e:
        logger.warning("Ошибка скачивания: %s", e)
        error_text = str(e)

        if "tiktok" in url.lower() and (
            "_solve_challenge" in error_text or "Unexpected response" in error_text
        ):
            await status_msg.edit_text(
                "TikTok сейчас не скачивается — это известная проблема на "
                "стороне библиотеки yt-dlp (TikTok обновил защиту от ботов, "
                "фикс ещё не вышел). Дело не в этом боте, попробуй позже — "
                "как только выйдет обновление, заработает само."
            )
            return

        error_snippet = error_text.splitlines()[-1][:400]
        await status_msg.edit_text(
            "Не смог скачать по этой ссылке 😕\n"
            "Либо ссылка не поддерживается, либо контент приватный/удалён.\n\n"
            f"Тех. детали (временно, для отладки):\n`{error_snippet}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.exception("Неожиданная ошибка при обработке ссылки %s", url)
        error_snippet = str(e)[:400]
        await status_msg.edit_text(
            "Что-то пошло не так. Попробуй ещё раз позже.\n\n"
            f"Тех. детали (временно, для отладки):\n`{error_snippet}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Мини health-check сервер (нужен, чтобы Render Free Web Service не считал
# сервис мёртвым — он ожидает, что порт открыт и отвечает на HTTP-запросы)
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):  # noqa: A002 - глушим лишние логи
        pass


def run_health_server() -> None:
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    logger.info("Health-check сервер запущен на порту %s", port)
    server.serve_forever()


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def build_app() -> Application:
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


def main() -> None:
    # HTTP-сервер для health-check — в отдельном потоке, не мешает боту.
    threading.Thread(target=run_health_server, daemon=True).start()

    app = build_app()
    logger.info("Бот запущен, начинаю polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
