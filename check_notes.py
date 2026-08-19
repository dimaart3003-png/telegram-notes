"""
Проверяет новые сообщения Telegram-бота и раскладывает их на идеи/задачи.

При первом запуске обрабатывает все сообщения, доступные через getUpdates
(история Telegram хранит их максимум 24 часа). При последующих запусках —
только те, что пришли после предыдущей проверки (используется offset,
сохранённый в state.json).
"""

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
STATE_FILE = BASE_DIR / "state.json"
IDEA_FILE = BASE_DIR / "Idea.txt"
TASK_FILE = BASE_DIR / "Zadacha.txt"

TASK_KEYWORDS = [
    "сделать", "купить", "позвонить", "написать", "отправить", "оплатить",
    "сходить", "забрать", "встретиться", "подготовить", "закончить",
    "убрать", "выполнить", "отвезти", "записать", "проверить", "заказать",
    "починить", "убраться", "приготовить", "решить", "договориться",
    "оформить", "подать", "запустить", "настроить", "созвониться",
    "зарубиться", "поиграть", "сыграть", "погонять", "забронировать",
    "посетить", "съездить", "заехать",
]


def load_token() -> str:
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("TELEGRAM_BOT_TOKEN не найден в .env")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_updates(token: str, offset: int | None) -> list:
    url = f"https://api.telegram.org/bot{token}/getUpdates?limit=100"
    if offset is not None:
        url += f"&offset={offset}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data["result"]


def classify(text: str) -> str:
    lowered = text.lower()
    for word in TASK_KEYWORDS:
        if re.search(rf"\b{word}\w*", lowered):
            return "task"
    return "idea"


def append_line(path: Path, date_str: str, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(f"[{date_str}] {text}\n")


def main() -> None:
    token = load_token()
    state = load_state()
    last_update_id = state.get("last_update_id")

    offset = last_update_id + 1 if last_update_id is not None else None
    updates = get_updates(token, offset)

    if not updates:
        print("Новых сообщений нет.")
        return

    IDEA_FILE.touch(exist_ok=True)
    TASK_FILE.touch(exist_ok=True)

    max_update_id = last_update_id or 0
    ideas, tasks = 0, 0

    for update in updates:
        max_update_id = max(max_update_id, update["update_id"])
        message = update.get("message")
        if not message or "text" not in message:
            continue

        text = message["text"]
        date_str = datetime.fromtimestamp(message["date"], tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")

        if classify(text) == "task":
            append_line(TASK_FILE, date_str, text)
            tasks += 1
        else:
            append_line(IDEA_FILE, date_str, text)
            ideas += 1

    save_state({"last_update_id": max_update_id})
    print(f"Обработано сообщений: {len(updates)} (идей: {ideas}, задач: {tasks})")


if __name__ == "__main__":
    main()
