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
import shutil
import logging
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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

# Telegram Bot API не даёт ботам отправлять файлы тяжелее ~50 МБ.
MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

URL_REGEX = re.compile(r"https?://[^\s]+")

SUPPORTED_HINT = (
    "Отправь мне ссылку на видео или фото из *TikTok*, *YouTube*, "
    "*Instagram*, *Twitter/X* и т.д.\n\n"
    "Я скачаю контент без водяных знаков и пришлю его сюда."
)


# ---------------------------------------------------------------------------
# Скачивание через yt-dlp
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
        # Многие площадки (TikTok, Instagram) отдают версию без водяного
        # знака именно через "родной" API, который yt-dlp и использует —
        # отдельная логика для watermark не нужна.
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    files = sorted(glob.glob(os.path.join(download_dir, "*")))
    return files


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
        files = await context.application.loop.run_in_executor(
            None, download_media, url, tmp_dir
        )

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

    except yt_dlp.utils.DownloadError as e:
        logger.warning("Ошибка скачивания: %s", e)
        await status_msg.edit_text(
            "Не смог скачать по этой ссылке 😕\n"
            "Либо ссылка не поддерживается, либо контент приватный/удалён."
        )
    except Exception:
        logger.exception("Неожиданная ошибка при обработке ссылки %s", url)
        await status_msg.edit_text("Что-то пошло не так. Попробуй ещё раз позже.")
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
