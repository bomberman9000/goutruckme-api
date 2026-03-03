from __future__ import annotations

from html import escape


def explain_health(health_data: dict) -> str:
    checks = health_data.get("checks", {}) or {}
    parser_metrics = (health_data.get("metrics", {}) or {}).get("parser", {}) or {}

    findings: list[str] = []
    actions: list[str] = []

    derived_checks = {"parser", "parser_queue", "parser_heartbeat", "activity", "memory"}
    critical_components = [
        name for name, status in checks.items()
        if "❌" in str(status) and name not in derived_checks
    ]
    if critical_components:
        findings.append(
            "Критично: недоступны компоненты: " + ", ".join(critical_components)
        )
        actions.append("Проверь сеть, Redis/PostgreSQL и контейнеры зависимых сервисов.")

    parser_hb = str(checks.get("parser_heartbeat", ""))
    parser_state = str(checks.get("parser", ""))
    queue_state = str(checks.get("parser_queue", ""))

    if "❌" in parser_hb:
        findings.append(
            "Парсер не подает heartbeat: parser-bot завис, ушел в restart-loop или не может дойти до Redis."
        )
        actions.append("Проверь логи parser-bot и наличие ключа parser:heartbeat.")
    elif "⚠️" in parser_hb:
        findings.append(
            "Heartbeat парсера нестабилен: давно не было успешного enqueue."
        )
        actions.append("Проверь, приходят ли новые сообщения в отслеживаемые чаты и не режет ли их фильтр.")

    if "No new events for" in parser_state:
        queue_depth = parser_metrics.get("queue_depth")
        pending = parser_metrics.get("pending")
        lag = parser_metrics.get("lag")
        if isinstance(queue_depth, int) and queue_depth == 0 and (lag in (0, None)) and (pending in (0, None)):
            findings.append(
                "Новых parser-событий давно нет: поток пуст. Либо источники молчат, либо parser-bot не кладет live-сообщения в stream."
            )
            actions.append("Сверь логи parser-bot с реальными сообщениями в Telegram-чатах.")
        else:
            findings.append(
                "Парсер давно не обновлял события при непустой очереди: parser-worker может отставать."
            )
            actions.append("Проверь parser-worker, Redis consumer group и retry-ошибки sync.")

    if "⚠️" in queue_state:
        findings.append(
            "Есть отставание очереди parser stream: worker не успевает разгребать поток."
        )
        actions.append("Проверь lag/pending в consumer group и ошибки в parser-worker.")

    manual_review = parser_metrics.get("manual_review")
    if isinstance(manual_review, int) and manual_review > 0:
        findings.append(
            f"В ручной проверке зависло {manual_review} сообщений."
        )
        actions.append("Открой /admin/manual-review и /admin/parser, чтобы разобрать очередь.")

    if not findings:
        last_age = parser_metrics.get("last_event_age_min")
        if isinstance(last_age, (int, float)):
            findings.append(
                f"Система выглядит стабильно: последний parser event был {last_age:.0f} мин назад."
            )
        else:
            findings.append("Система выглядит стабильно: критичных признаков не видно.")
        actions.append("Наблюдение: ручное вмешательство не требуется.")

    parts = ["<b>AI Diagnosis</b>"]
    parts.extend(f"• {escape(item)}" for item in findings[:3])
    parts.append("")
    parts.append("<b>Что делать</b>")
    deduped_actions: list[str] = []
    for item in actions:
        if item not in deduped_actions:
            deduped_actions.append(item)
    parts.extend(f"• {escape(item)}" for item in deduped_actions[:3])
    return "\n".join(parts)
