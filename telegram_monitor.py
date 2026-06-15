#!/usr/bin/env python3
"""
Telegram Monitor Script
Мониторинг чатов Telegram аккаунта отдела продаж для контроля своевременности ответов клиентам.
"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from telethon import TelegramClient
from telethon.tl.types import User, Chat, Channel, Message
from loguru import logger

from response_time import compute_response_times_hours, filter_by_min_pairs


# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

VALID_REPORT_TYPES = {"unread", "unanswered", "leaderboard"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    api_id: int
    api_hash: str
    phone: str
    report_to: str = "@victorryakh"
    report_to_thread: Optional[int] = None
    excluded_chats: list[str] = ["@PremiumBot", "@SpamBot"]
    report_types: list[str] = ["unread", "unanswered"]
    leaderboard_response_list_count: int = 10
    leaderboard_response_window_days: int = 7
    leaderboard_min_pairs: int = 3
    leaderboard_response_messages_count: int = 100

    @field_validator("report_types", mode="before")
    @classmethod
    def parse_report_types(cls, v: object) -> list[str]:
        if isinstance(v, str):
            v = [item.strip() for item in v.split(",") if item.strip()]
        if isinstance(v, list):
            unknown = set(v) - VALID_REPORT_TYPES
            if unknown:
                raise ValueError(f"Неизвестные типы отчётов: {unknown}. Допустимые: {VALID_REPORT_TYPES}")
        return v


settings = Settings()


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def format_duration(hours: float) -> str:
    total_minutes = int(hours * 60)
    h = total_minutes // 60
    m = total_minutes % 60
    if h > 0:
        return f"{h}ч {m}м"
    return f"{m}м"


def get_color_indicator(elapsed_hours: float) -> str:
    if elapsed_hours < 2:
        return "🔵"
    elif elapsed_hours < 4:
        return "🟡"
    else:
        return "🔴"


def format_chat_name(entity) -> str:
    """
    Форматирует имя чата для отображения в отчете.

    Args:
        entity: Объект чата (User, Chat, Channel)

    Returns:
        Отформатированное имя
    """
    if isinstance(entity, User):
        username = f"@{entity.username}" if entity.username else "Нет username"
        name = f"{entity.first_name or ''} {entity.last_name or ''}".strip() or "Без имени"
        return f"{username} ({name})"
    elif isinstance(entity, (Chat, Channel)):
        title = entity.title or "Без названия"
        username = f"@{entity.username}" if hasattr(entity, 'username') and entity.username else "Нет username"
        return f"{username} ({title})"
    return "Неизвестный чат"


async def has_our_reaction(message: Message, client: TelegramClient) -> bool:
    """
    Проверяет, есть ли наша реакция на сообщение.

    Args:
        message: Объект сообщения
        client: Клиент Telegram

    Returns:
        True если есть наша реакция, False иначе
    """
    if not message.reactions:
        return False

    me = await client.get_me()

    for reaction in message.reactions.results:
        try:
            if message.reactions.recent_reactions:
                for recent in message.reactions.recent_reactions:
                    if recent.peer_id.user_id == me.id:
                        return True
        except Exception as e:
            logger.debug(f"Ошибка при проверке реакций: {e}")
            continue

    return False


# ============================================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================================

async def calculate_chat_avg_response_time(
    dialog, client: TelegramClient, me, current_time: datetime,
) -> Optional[Tuple[str, float, int]]:
    """
    Считает среднее время ответа менеджера за окно `leaderboard_response_window_days`.

    Returns:
        (chat_name, avg_hours, pairs_count) или None, если пар «вопрос→ответ» нет.
    """
    try:
        entity = dialog.entity
        chat_name = format_chat_name(entity)

        window_start = current_time - timedelta(days=settings.leaderboard_response_window_days)
        messages = await client.get_messages(
            entity,
            offset_date=window_start,
            limit=settings.leaderboard_response_messages_count,
        )
        if not messages:
            return None

        messages_sorted = sorted(messages, key=lambda m: m.date)

        response_times = compute_response_times_hours(messages_sorted, me.id)
        if not response_times:
            return None

        avg_hours = sum(response_times) / len(response_times)
        return (chat_name, avg_hours, len(response_times))

    except Exception as e:
        logger.error(f"Ошибка при расчёте времени ответа для {dialog.name}: {e}")
        return None


async def analyze_chat(dialog, client: TelegramClient, current_time: datetime) -> Tuple[Optional[Tuple[str, float]], Optional[Tuple[str, float]]]:
    """
    Анализирует чат на наличие непрочитанных или неотвеченных сообщений.

    Args:
        dialog: Объект диалога
        client: Клиент Telegram
        current_time: Текущее время

    Returns:
        Кортеж (unread_info, unanswered_info) или (None, None)
    """
    try:
        entity = dialog.entity
        chat_name = format_chat_name(entity)

        messages = await client.get_messages(entity, limit=1)
        if not messages:
            return None, None

        last_message = messages[0]
        me = await client.get_me()

        is_from_client = last_message.sender_id != me.id

        unread_info = None
        unanswered_info = None

        if dialog.unread_count > 0:
            unread_messages = await client.get_messages(entity, limit=dialog.unread_count)
            if unread_messages:
                first_unread = unread_messages[-1]
                elapsed_hours = (current_time - first_unread.date.astimezone()).total_seconds() / 3600
                unread_info = (
                    f"{chat_name} - {dialog.unread_count} непрочитанных, первое от {first_unread.date.astimezone().strftime('%d.%m.%Y %H:%M')}",
                    elapsed_hours,
                )
                logger.warning(f"Непрочитанные сообщения: {unread_info[0]}")

        if is_from_client:
            elapsed_hours = (current_time - last_message.date.astimezone()).total_seconds() / 3600
            has_reaction = await has_our_reaction(last_message, client)

            if not has_reaction:
                unanswered_info = (
                    f"{chat_name} - последнее сообщение от {last_message.date.astimezone().strftime('%d.%m.%Y %H:%M')}",
                    elapsed_hours,
                )
                logger.warning(f"Неотвеченное сообщение: {unanswered_info[0]}")
            else:
                logger.info(f"Сообщение от {chat_name} имеет реакцию, пропускаем")

        return unread_info, unanswered_info

    except Exception as e:
        logger.error(f"Ошибка при анализе чата {dialog.name}: {e}")
        return None, None


async def _send_chunks(client: TelegramClient, lines: List[str], label: str) -> None:
    max_len = 4000
    chunks: List[str] = []
    current: List[str] = []
    current_length = 0
    for line in lines:
        if current and current_length + len(line) > max_len:
            chunks.append("".join(current))
            current = [line]
            current_length = len(line)
        else:
            current.append(line)
            current_length += len(line)
    if current:
        chunks.append("".join(current))

    for index, chunk in enumerate(chunks, start=1):
        if settings.report_to_thread is not None:
            await client.send_message(
                settings.report_to, chunk, comment_to=settings.report_to_thread
            )
        else:
            await client.send_message(settings.report_to, chunk)
        logger.info(f"{label} отправлен(а) {settings.report_to} (часть {index}/{len(chunks)})")


async def send_unread_report(client: TelegramClient, unread_list: List[Tuple[str, float]]) -> None:
    try:
        now_str = datetime.now().astimezone().strftime('%d.%m.%Y %H:%M')
        lines: List[str] = []
        if unread_list:
            lines.append(f"📬 НЕПРОЧИТАННЫЕ СООБЩЕНИЯ ({len(unread_list)}) — {now_str}\n\n")
            for text, hours in unread_list:
                lines.append(f"{get_color_indicator(hours)} {text}\n")
        else:
            lines.append(f"✅ Непрочитанных сообщений нет — {now_str}\n")
            logger.info("Непрочитанных сообщений не найдено")
        await _send_chunks(client, lines, "Отчёт по непрочитанным")
    except Exception as e:
        logger.error(f"Ошибка при отправке отчёта по непрочитанным: {e}", exc_info=True)


async def send_unanswered_report(client: TelegramClient, unanswered_list: List[Tuple[str, float]]) -> None:
    try:
        now_str = datetime.now().astimezone().strftime('%d.%m.%Y %H:%M')
        lines: List[str] = []
        if unanswered_list:
            lines.append(f"💬 НЕОТВЕЧЕННЫЕ СООБЩЕНИЯ ({len(unanswered_list)}) — {now_str}\n\n")
            for text, hours in unanswered_list:
                lines.append(f"{get_color_indicator(hours)} {text}\n")
            lines.append("\nЧтобы сообщения не считались неотвеченными — ставь реакцию в конце сообщения собеседника")
        else:
            lines.append(f"✅ Неотвеченных сообщений нет — {now_str}\n")
            logger.info("Неотвеченных сообщений не найдено")
        await _send_chunks(client, lines, "Отчёт по неотвеченным")
    except Exception as e:
        logger.error(f"Ошибка при отправке отчёта по неотвеченным: {e}", exc_info=True)


async def send_leaderboard_report(client: TelegramClient, leaderboard: List[Tuple[str, float, int]]) -> None:
    try:
        now_str = datetime.now().astimezone().strftime('%d.%m.%Y %H:%M')
        lines: List[str] = []
        lines.append(
            f"🏆 ТОП-{settings.leaderboard_response_list_count} ПЕРЕПИСОК ПО СКОРОСТИ ОТВЕТА МЕНЕДЖЕРА\n"
            f"(окно: {settings.leaderboard_response_window_days} дн., "
            f"мин. пар: {settings.leaderboard_min_pairs}, {now_str})\n\n"
        )
        if leaderboard:
            medals = ["🥇", "🥈", "🥉"]
            for rank, (chat_name, avg_hours, pairs_count) in enumerate(leaderboard, start=1):
                icon = medals[rank - 1] if rank <= 3 else f"{rank}."
                lines.append(
                    f"{icon} {chat_name} — ср. ответ: {format_duration(avg_hours)} "
                    f"({pairs_count} пар)\n"
                )
        else:
            lines.append(
                f"Недостаточно данных: нет чатов с ≥{settings.leaderboard_min_pairs} пар "
                f"«клиент→менеджер» за последние {settings.leaderboard_response_window_days} дн."
            )
        await _send_chunks(client, lines, "Топ ответов")
    except Exception as e:
        logger.error(f"Ошибка при отправке топа ответов: {e}", exc_info=True)


async def monitor_chats():
    """
    Основная функция мониторинга чатов.
    """
    logger.info("=" * 80)
    logger.info("Запуск мониторинга Telegram чатов")
    logger.info("=" * 80)

    current_time = datetime.now().astimezone()
    logger.info(f"Текущее время: {current_time.strftime('%d.%m.%Y %H:%M:%S %Z')}")

    logger.info(f"Типы отчётов: {', '.join(settings.report_types)}")
    logger.info("Начинаем проверку чатов...")

    client = TelegramClient('session', settings.api_id, settings.api_hash)

    try:
        await client.start(phone=settings.phone)
        logger.info("Успешное подключение к Telegram")

        me = await client.get_me()
        logger.info(f"Авторизован как: {me.first_name} (@{me.username})")

        dialogs = await client.get_dialogs()
        logger.info(f"Получено диалогов: {len(dialogs)}")

        need_unread = "unread" in settings.report_types
        need_unanswered = "unanswered" in settings.report_types
        need_leaderboard = "leaderboard" in settings.report_types
        need_chat_analysis = need_unread or need_unanswered

        unread_list: List[Tuple[str, float]] = []
        unanswered_list: List[Tuple[str, float]] = []
        leaderboard_raw: List[Tuple[str, float, int]] = []

        checked_count = 0

        for dialog in dialogs:
            entity = dialog.entity

            if dialog.archived:
                logger.debug(f"Пропуск архивированного чата: {format_chat_name(entity)}")
                continue

            if isinstance(entity, Channel) and entity.broadcast:
                continue

            chat_id = dialog.id
            chat_username = f"@{entity.username}" if hasattr(entity, 'username') and entity.username else None
            chat_title = entity.title if hasattr(entity, 'title') else None

            if chat_id in settings.excluded_chats or chat_username in settings.excluded_chats or chat_title in settings.excluded_chats:
                logger.debug(f"Пропуск исключенного чата: {format_chat_name(entity)}")
                continue

            if need_leaderboard:
                result = await calculate_chat_avg_response_time(dialog, client, me, current_time)
                if result is not None:
                    leaderboard_raw.append(result)

            if need_chat_analysis:
                unread_info, unanswered_info = await analyze_chat(dialog, client, current_time)
                if need_unread and unread_info:
                    unread_list.append(unread_info)
                if need_unanswered and unanswered_info:
                    unanswered_list.append(unanswered_info)

            checked_count += 1

        if need_leaderboard:
            logger.warning(
                "leaderboard: window=%d days, min_pairs=%d (replaces 'last %d messages')",
                settings.leaderboard_response_window_days,
                settings.leaderboard_min_pairs,
                settings.leaderboard_response_messages_count,
            )
            leaderboard_list, excluded_by_min_pairs = filter_by_min_pairs(
                leaderboard_raw, settings.leaderboard_min_pairs,
            )

        logger.info(f"Проверено чатов: {checked_count}")

        if need_unread:
            unread_list.sort(key=lambda x: x[1], reverse=True)
            logger.info(f"Найдено непрочитанных: {len(unread_list)}")
            await send_unread_report(client, unread_list)

        if need_unanswered:
            unanswered_list.sort(key=lambda x: x[1], reverse=True)
            logger.info(f"Найдено неотвеченных: {len(unanswered_list)}")
            await send_unanswered_report(client, unanswered_list)

        if need_leaderboard:
            leaderboard_list.sort(key=lambda x: x[1])
            top = leaderboard_list[:settings.leaderboard_response_list_count]
            logger.info(
                f"Лидерборд: {len(leaderboard_list)} чатов прошли порог "
                f"({excluded_by_min_pairs} исключено по мин. {settings.leaderboard_min_pairs} пар), "
                f"в топ вошло {len(top)}"
            )
            await send_leaderboard_report(client, top)

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise
    finally:
        await client.disconnect()
        logger.info("Отключение от Telegram")


# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

def main():
    """
    Точка входа в программу.
    """
    try:
        asyncio.run(monitor_chats())
        logger.info("Мониторинг завершен успешно")
    except KeyboardInterrupt:
        logger.info("Мониторинг прерван пользователем")
    except Exception as e:
        logger.error(f"Необработанная ошибка: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()