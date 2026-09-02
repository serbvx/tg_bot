import os
import re
import asyncio
import logging
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from yt_dlp import YoutubeDL

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("BOT_TOKEN", "8858324377:AAH_yy4akPd2rl0A4JBzKqhecC1oJwutnAI")
URL_REGEX = r'(https?://[^\s]+)'

# Минимальный веб-сервер для проходимости Health Check на Render
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **Привет! Я MediaGrabber Pro.**\n\n"
        "Я могу скачивать медиафайлы без водяных знаков из:\n"
        "• **TikTok**\n"
        "• **Instagram Reels**\n"
        "• **YouTube Shorts**\n"
        "• **VK Clips**\n"
        "• **Pinterest** и др.\n\n"
        "📥 **Просто отправь мне ссылку на видео.**"
    )
    keyboard = [[InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    help_text = (
        "📌 **Как пользоваться ботом:**\n"
        "1. Скопируйте ссылку на видео.\n"
        "2. Отправьте её в чат.\n\n"
        "⚠️ *Лимит размера файла в Telegram — 50 МБ.*"
    )
    await query.message.reply_text(help_text, parse_mode="Markdown")

async def download_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    match = re.search(URL_REGEX, text)
    if not match:
        return

    url = match.group(1)
    status_msg = await update.message.reply_text("🔎 *Анализирую ссылку...*", parse_mode="Markdown")

    file_prefix = f"media_{update.message.message_id}"
    outtmpl = f"{file_prefix}.%(ext)s"

    ydl_opts = {
        'format': 'b[filesize<48M]/b/best',
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
    }

    try:
        await status_msg.edit_text("⏳ *Скачиваю файл...*", parse_mode="Markdown")
        loop = asyncio.get_running_loop()

        def extract():
            with YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=True)

        info = await loop.run_in_executor(None, extract)
        filename = None
        
        for file in os.listdir("."):
            if file.startswith(file_prefix):
                filename = file
                break

        if filename and os.path.exists(filename):
            file_size = os.path.getsize(filename) / (1024 * 1024)
            if file_size > 49.5:
                await status_msg.edit_text("❌ Файл превышает лимит 50 МБ.")
                os.remove(filename)
                return

            await status_msg.edit_text("📤 *Отправляю медиа...*", parse_mode="Markdown")
            
            with open(filename, 'rb') as f:
                if filename.endswith(('.mp3', '.m4a', '.opus')):
                    await update.message.reply_audio(audio=f, caption="⚡️ Скачано с помощью бота")
                else:
                    await update.message.reply_video(video=f, caption="⚡️ Скачано с помощью бота")

            await status_msg.delete()
            os.remove(filename)
        else:
            await status_msg.edit_text("❌ Не удалось обработать файл.")

    except Exception as e:
        logging.error(f"Error downloading: {e}")
        await status_msg.edit_text("❌ Ошибка при скачивании.")
        for file in os.listdir("."):
            if file.startswith(file_prefix):
                os.remove(file)

async def main():
    await start_web_server()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(help_callback, pattern="^help$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_media))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
