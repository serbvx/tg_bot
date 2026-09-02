# TG Downloader Bot

Telegram-бот для скачивания видео и фото **без водяных знаков** из TikTok,
YouTube, Instagram, Twitter/X и других сайтов, которые поддерживает
[yt-dlp](https://github.com/yt-dlp/yt-dlp).

Просто пришли боту ссылку — он скачает и пришлёт файл в ответ.

## Возможности

- TikTok, YouTube (Shorts тоже), Instagram (Reels/посты), Twitter/X и др.
- Автоматически определяет видео это или фото/карусель
- Работает бесплатно на Render.com

## ⚠️ Важно про токен

Токен бота — это секрет уровня пароля. Он хранится **только** в переменной
окружения `BOT_TOKEN` и никогда не должен попадать в код или коммититься в
Git. Файл `.env.example` — просто образец, реальный `.env` в репозиторий не
кладём (он и так в `.gitignore`).

Если токен когда-либо "засветился" (в чате, в коде, в скриншоте) — сразу
перевыпусти его через [@BotFather](https://t.me/BotFather):
`/mybots` → бот → `Bot Settings` → `API Token` → `Revoke current token`.

## Локальный запуск

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

export BOT_TOKEN=твой_токен      # Windows (PowerShell): $env:BOT_TOKEN="твой_токен"
python bot.py
```

## Деплой на GitHub + Render.com (бесплатно)

### 1. Публикация на GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/ТВОЙ_НИК/tg-downloader-bot.git
git push -u origin main
```

### 2. Деплой на Render

1. Зайди на [render.com](https://render.com) и залогинься (можно через GitHub).
2. **New +** → **Web Service**.
3. Выбери репозиторий `tg-downloader-bot`.
4. Render сам подхватит настройки из `render.yaml` (build/start команды,
   план — Free). Если спросит вручную:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
   - **Instance Type:** Free
5. Во вкладке **Environment** добавь переменную:
   - `BOT_TOKEN` = твой настоящий токен от BotFather
6. Нажми **Create Web Service** — Render соберёт и запустит бота.

### Почему это "Web Service", а не просто скрипт

Бесплатный план Render требует, чтобы сервис слушал HTTP-порт — иначе он
считает его нерабочим. Поэтому в `bot.py` поднят маленький сервер
"health-check" в фоновом потоке, а сам бот при этом работает через
long polling (постоянно спрашивает Telegram о новых сообщениях). Отдельно
настраивать webhook не нужно.

**Нюанс бесплатного плана:** Render "усыпляет" бесплатные сервисы после
~15 минут без входящих HTTP-запросов. Long polling обычно поддерживает
соединение активным, но если заметишь, что бот "засыпает", самый простой
вариант — настроить внешний пинг health-check URL раз в 10 минут (например,
через [UptimeRobot](https://uptimerobot.com), бесплатно) на адрес
`https://твой-сервис.onrender.com/`.

## Ограничения

- Telegram не позволяет ботам отправлять файлы тяжелее **50 МБ** — для очень
  длинных/тяжёлых видео бот сообщит, что файл слишком большой.
- Приватный или удалённый контент скачать нельзя.
- Некоторые площадки периодически меняют защиту — если что-то перестало
  скачиваться, первым делом попробуй обновить `yt-dlp`
  (`pip install -U yt-dlp`), это самая частая причина.

## Структура проекта

```
tg-downloader-bot/
├── bot.py              # логика бота
├── requirements.txt    # зависимости
├── render.yaml         # конфиг деплоя для Render
├── .gitignore
├── .env.example
└── README.md
```
