import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import os

# Токен из переменных окружения
TOKEN = os.environ.get("TELEGRAM_TOKEN")

if not TOKEN:
    print("❌ Нет токена!")
    exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Простые ответы
answers = {
    "привет": "👋 Привет! Я бот 24/7!",
    "как дела": "✅ Всё отлично! А у тебя?",
    "помощь": "📚 /start, /help — просто пиши",
    "2+2": "2 + 2 = 4 ✅",
}

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(f"👋 Привет, {message.from_user.first_name}!\n\nЯ бот 24/7! Просто пиши.")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer("📚 /start, /help — просто пиши")

@dp.message()
async def handle(message: types.Message):
    text = message.text.lower()
    
    # Проверяем картинки
    if message.photo:
        await message.answer("🖼️ Картинка получена!")
        return
    
    # Проверяем текст
    for key, answer in answers.items():
        if key in text:
            await message.answer(answer)
            return
    
    await message.answer(f"Ты написал: {message.text}")

async def main():
    print("✅ Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
