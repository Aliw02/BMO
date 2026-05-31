# OpenCode Telegram Bot

A Telegram bot that bridges your local OpenCode instance to Telegram chats.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then edit .env with your bot token
python main.py
```

## Configuration (.env)

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | Your Telegram bot token from @BotFather |
| `OPENCODE_HOST` | OpenCode host (default: 127.0.0.1) |
| `OPENCODE_PORT` | OpenCode port (default: 4096) |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs allowed to use the bot (empty = everyone) |
| `DEBUG` | Enable debug logging (default: False) |

## Commands

| Command | Description |
|---|---|
| `/start` | Reconnect & welcome |
| `/help` | Show help |
| `/clear` | Clear chat history |
| `/status` | Show OpenCode connection status |

## Project Structure

```
├── main.py                 Entry point
├── config/
│   └── settings.py         Configuration & env loading
├── core/
│   └── bot_client.py       OpenCode subprocess client
├── handlers/
│   └── messages.py         Telegram message handlers
├── models/
│   └── chat_models.py      Data models (ChatSession, ChatMessage, UserMemory)
├── storage/
│   └── storage.py          JSON storage backend
├── data/                   Chat history (auto-created)
├── logs/                   Log files (auto-created)
├── requirements.txt
├── .env.example
└── run_bot.bat             Windows launcher
```
