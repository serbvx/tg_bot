import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import os

TOKEN = os.environ.get("TELEGRAM_TOKEN")

if not TOKEN:
    print("❌ Нет токена!")
    exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("👋 Привет! Я бот 24/7! Отправляй текст или картинки!")

@dp.message()
async def handle(message: types.Message):
    if message.photo:
        await message.answer("🖼️ Картинка получена!")
    else:
        await message.answer(f"Ты написал: {message.text}")

async def main():
    print("✅ Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
