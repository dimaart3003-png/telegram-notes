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
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
STATE_FILE = BASE_DIR / "state.json"
IDEA_FILE = BASE_DIR / "Idea.txt"
TASK_FILE = BASE_DIR / "Zadacha.txt"
VOICES_DIR = BASE_DIR / "voices"

# "base" — компромисс точности/памяти для сервера с ~1 ГБ RAM + 2 ГБ swap.
WHISPER_MODEL_NAME = "base"
_whisper_model = None

VOICE_MAX_AGE_DAYS = 30

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


def download_file(token: str, file_id: str, dest: Path) -> None:
    url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"getFile error: {data}")
    file_path = data["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    urllib.request.urlretrieve(file_url, dest)


def cleanup_old_voices(max_age_days: int = VOICE_MAX_AGE_DAYS) -> int:
    if not VOICES_DIR.exists():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for f in VOICES_DIR.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    return removed


def transcribe(path: Path) -> str:
    global _whisper_model
    import whisper  # локальный импорт: не грузим torch, если голосовых нет

    if _whisper_model is None:
        _whisper_model = whisper.load_model(WHISPER_MODEL_NAME)
    result = _whisper_model.transcribe(str(path), language="ru")
    return result["text"].strip()


def main() -> None:
    token = load_token()
    state = load_state()
    last_update_id = state.get("last_update_id")

    offset = last_update_id + 1 if last_update_id is not None else None
    updates = get_updates(token, offset)

    removed = cleanup_old_voices()

    if not updates:
        msg = "Новых сообщений нет."
        if removed:
            msg += f" Удалено старых записей (>{VOICE_MAX_AGE_DAYS} дн.): {removed}"
        print(msg)
        return

    IDEA_FILE.touch(exist_ok=True)
    TASK_FILE.touch(exist_ok=True)
    VOICES_DIR.mkdir(exist_ok=True)

    max_update_id = last_update_id or 0
    ideas, tasks, voices, failed = 0, 0, 0, 0

    for update in updates:
        max_update_id = max(max_update_id, update["update_id"])
        message = update.get("message")
        if not message:
            continue

        text = message.get("text")
        date_str = datetime.fromtimestamp(message["date"], tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")

        if text is None and "voice" in message:
            filename = f"{date_str.replace(' ', '_').replace(':', '-')}_{update['update_id']}.oga"
            voice_path = VOICES_DIR / filename
            try:
                download_file(token, message["voice"]["file_id"], voice_path)
                text = transcribe(voice_path)
                voices += 1
            except Exception as e:
                print(f"Не удалось распознать голосовое сообщение: {e}")
                failed += 1
                voice_path.unlink(missing_ok=True)
                continue

        if not text:
            continue

        if classify(text) == "task":
            append_line(TASK_FILE, date_str, text)
            tasks += 1
        else:
            append_line(IDEA_FILE, date_str, text)
            ideas += 1

    save_state({"last_update_id": max_update_id})

    summary = (
        f"Обработано сообщений: {len(updates)} "
        f"(идей: {ideas}, задач: {tasks}, голосовых распознано: {voices}, ошибок распознавания: {failed})"
    )
    if removed:
        summary += f", удалено старых записей (>{VOICE_MAX_AGE_DAYS} дн.): {removed}"
    print(summary)


if __name__ == "__main__":
    main()
