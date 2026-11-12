"""Telegram-бот для агента «Мой старт ИП» (long polling, только стандартная библиотека).

Запуск:  TELEGRAM_BOT_TOKEN=123:ABC python telegram_bot.py
Токен — у @BotFather. LLM настраивается теми же переменными, что и веб-версия.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from app import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from agent.core import Agent, GREETING  # noqa: E402

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API = f"https://api.telegram.org/bot{TOKEN}"


def call(method: str, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"{API}/{method}", data=data, timeout=70) as r:
        return json.loads(r.read().decode())


def main():
    if not TOKEN:
        sys.exit("Задайте TELEGRAM_BOT_TOKEN (получить у @BotFather)")
    agent = Agent()
    print(f"Бот запущен, режим: {agent.mode}")
    offset = 0
    while True:
        try:
            upd = call("getUpdates", offset=offset, timeout=50)
        except Exception as e:
            print("Ошибка сети:", e)
            time.sleep(3)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            text, chat = msg.get("text"), msg.get("chat", {}).get("id")
            if not text or not chat:
                continue
            if text == "/start":
                agent.sessions.reset(str(chat))
                reply = GREETING
            else:
                call("sendChatAction", chat_id=chat, action="typing")
                reply = agent.handle(str(chat), text)["reply"]
            for i in range(0, len(reply), 4000):
                call("sendMessage", chat_id=chat, text=reply[i:i + 4000])


if __name__ == "__main__":
    main()
