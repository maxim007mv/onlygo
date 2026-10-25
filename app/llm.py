"""Optional OpenAI-compatible LLM client, configured only via env vars.
If OPENAI_API_KEY is empty or any call fails, callers fall back to rule-based logic."""

from __future__ import annotations

import json
import logging
import os

import httpx

log = logging.getLogger("onlygo.llm")


def enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def chat(messages: list[dict], json_mode: bool = False, timeout: float = 25.0) -> str | None:
    if not enabled():
        return None
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    body = {"model": model(), "messages": messages, "temperature": 0.2}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        r = httpx.post(
            f"{base}/chat/completions", json=body, timeout=timeout, headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"}
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001 - any failure -> graceful fallback
        log.warning("LLM call failed, falling back to rules: %s", e)
        return None


PARSE_PROMPT = """Ты — модуль понимания запросов тревел-агента. Извлеки из сообщения путешественника ТОЛЬКО
явно или неявно упомянутые параметры и верни JSON-объект (без лишних ключей, только упомянутые):
origin (one of: moscow, spb, kazan, ekb, nsk), destination (one of: {dest_ids}), exclude_destinations (list),
month (1-12), start_date (YYYY-MM-DD, сегодня {today}), nights (int), shift_days (int, сдвиг дат: "на 2 дня позже" => 2),
extend_nights (int), budget (int RUB, на всех), budget_factor (float, "бюджет меньше" => 0.8), price_sensitive (bool),
adults (int), children (int), child_ages (list int), pets (bool),
max_travel_hours (int, "без долгих перелётов" => 5), direct_only (bool), visa_free_only (bool), prefer_train (bool),
meals (RO|BB|HB|AI), flexible_cancellation (bool), beach_close (bool), center_close (bool),
moods (list из: warm, sea, beach, mountains, ski, city, food, calm, party, romantic, nature, luxury, diving),
change_destination (bool), change_hotel (bool), dates_flexible (bool), implicit_notes (list строк по-русски:
неявные потребности, которые ты вывел).
Текущие требования (для контекста правок): {state}"""


def parse(text: str, today: str, state: dict, dest_ids: list[str]) -> dict | None:
    out = chat(
        [
            {
                "role": "system",
                "content": PARSE_PROMPT.format(today=today, state=json.dumps(state, ensure_ascii=False), dest_ids=", ".join(dest_ids)),
            },
            {"role": "user", "content": text},
        ],
        json_mode=True,
    )
    if not out:
        return None
    try:
        data = json.loads(out)
        return {k: v for k, v in data.items() if v not in (None, "", [])}
    except json.JSONDecodeError:
        return None


def explain(summary: dict) -> str | None:
    return chat(
        [
            {
                "role": "system",
                "content": "Ты — дружелюбный тревел-агент. По структурированным данным напиши 3-5 коротких "
                "предложений по-русски: что ты подобрал, почему это подходит именно этому путешественнику, чем пожертвовали и какие риски. "
                "Не выдумывай факты, используй только данные. Без markdown-заголовков.",
            },
            {"role": "user", "content": json.dumps(summary, ensure_ascii=False)},
        ]
    )
