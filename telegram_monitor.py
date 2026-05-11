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
    working_hours_start: int = 9
    working_hours_end: int = 21
    excluded_chats: list[str] = ["@PremiumBot", "@SpamBot"]
    report_types: list[str] = ["unread", "unanswered"]
    leaderboard_response_list_count: int = 10
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

    @property
    def working_hours(self) -> tuple[int, int]:
        return (self.working_hours_start, self.working_hours_end)


settings = Settings()


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def calculate_working_hours_passed(
    message_time: datetime,
    current_time: datetime,
    working_hours: Tuple[int, int]
) -> float:
    """
    Вычисляет количество рабочих часов, прошедших между двумя моментами времени.

    Args:
        message_time: Время отправки сообщения
        current_time: Текущее время
        working_hours: Кортеж (начало, конец) рабочего дня в часах

    Returns:
        Количество рабочих часов (float)
    """
    start_hour, end_hour = working_hours
    total_working_hours = 0.0

    # Начинаем с времени сообщения
    current = message_time

    while current < current_time:
        # Определяем конец текущего дня
        end_of_day = current.replace(hour=end_hour, minute=0, second=0, microsecond=0)

        # Если текущее время до начала рабочего дня, переходим к началу рабочего дня
        if current.hour < start_hour:
            current = current.replace(hour=start_hour, minute=0, second=0, microsecond=0)

        # Если текущее время после конца рабочего дня, переходим к следующему дню
        if current.hour >= end_hour:
            next_day = current + timedelta(days=1)
            current = next_day.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            continue

        # Определяем конец периода для подсчета
        if current_time < end_of_day:
            end_period = current_time
        else:
            end_period = end_of_day

        # Если конец периода в рабочее время, считаем часы
        if end_period.hour < end_hour or (end_period.hour == end_hour and end_period.minute == 0):
            if current < end_period:
                hours_diff = (end_period - current).total_seconds() / 3600
                total_working_hours += hours_diff

        # Переходим к следующему дню
        if end_period >= end_of_day:
            next_day = current + timedelta(days=1)
            current = next_day.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        else:
            break

    return total_working_hours


def is_working_hours(current_time: datetime, working_hours: Tuple[int, int]) -> bool:
    """
    Проверяет, находится ли текущее время в рабочих часах.

    Args:
        current_time: Текущее время
        working_hours: Кортеж (начало, конец) рабочего дня в часах

    Returns:
        True если рабочее время, False иначе
    """
    start_hour, end_hour = working_hours
    return start_hour <= current_time.hour < end_hour


def format_duration(hours: float) -> str:
    total_minutes = int(hours * 60)
    h = total_minutes // 60
    m = total_minutes % 60
    if h > 0:
        return f"{h}ч {m}м"
    return f"{m}м"


def get_color_indicator(working_hours: float) -> str:
    if working_hours < 2:
        return "🔵"
    elif working_hours < 4:
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

    # Проверяем все реакции на сообщение
    for reaction in message.reactions.results:
        # Получаем список пользователей, поставивших эту реакцию
        try:
            # Для некоторых типов реакций может потребоваться дополнительная проверка
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

async def calculate_chat_avg_response_time(dialog, client: TelegramClient, me) -> Optional[Tuple[str, float]]:
    """
    Считает среднее рабочее время ответа менеджера за последние N сообщений чата.

    Returns:
        (chat_name, avg_working_hours) или None, если пар «вопрос→ответ» нет.
    """
    try:
        entity = dialog.entity
        chat_name = format_chat_name(entity)

        messages = await client.get_messages(entity, limit=settings.leaderboard_response_messages_count)
        if not messages:
            return None

        # Сортируем от старых к новым
        messages_sorted = sorted(messages, key=lambda m: m.date)

        response_times: List[float] = []
        for i, msg in enumerate(messages_sorted):
            if msg.sender_id == me.id:
                continue  # сообщение от нас — не начало пары
            # Ищем следующий ответ менеджера
            for j in range(i + 1, len(messages_sorted)):
                next_msg = messages_sorted[j]
                if next_msg.sender_id == me.id:
                    hours = calculate_working_hours_passed(msg.date.astimezone(), next_msg.date.astimezone(), settings.working_hours)
                    response_times.append(hours)
                    break

        if not response_times:
            return None

        avg_hours = sum(response_times) / len(response_times)
        return (chat_name, avg_hours)

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

        # Получаем последнее сообщение
        messages = await client.get_messages(entity, limit=1)
        if not messages:
            return None, None

        last_message = messages[0]
        me = await client.get_me()

        # Проверяем, является ли последнее сообщение от клиента (не от нас)
        is_from_client = last_message.sender_id != me.id

        unread_info = None
        unanswered_info = None

        # Проверка непрочитанных сообщений
        if dialog.unread_count > 0:
            # Получаем первое непрочитанное сообщение
            unread_messages = await client.get_messages(entity, limit=dialog.unread_count)
            if unread_messages:
                first_unread = unread_messages[-1]  # Самое старое непрочитанное
                working_hours_passed = calculate_working_hours_passed(
                    first_unread.date.astimezone(),
                    current_time,
                    settings.working_hours
                )
                unread_info = (
                    f"{chat_name} - {dialog.unread_count} непрочитанных, первое от {first_unread.date.astimezone().strftime('%d.%m.%Y %H:%M')}",
                    working_hours_passed,
                )
                logger.warning(f"Непрочитанные сообщения: {unread_info[0]}")

        # Проверка неотвеченных сообщений
        if is_from_client:
            working_hours_passed = calculate_working_hours_passed(
                last_message.date.astimezone(),
                current_time,
                settings.working_hours
            )
            # Проверяем наличие реакции
            has_reaction = await has_our_reaction(last_message, client)

            if not has_reaction:
                unanswered_info = (
                    f"{chat_name} - последнее сообщение от {last_message.date.astimezone().strftime('%d.%m.%Y %H:%M')}",
                    working_hours_passed,
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


async def send_leaderboard_report(client: TelegramClient, leaderboard: List[Tuple[str, float]]) -> None:
    try:
        now_str = datetime.now().astimezone().strftime('%d.%m.%Y %H:%M')
        lines: List[str] = []
        lines.append(
            f"🏆 ТОП-{settings.leaderboard_response_list_count} ПЕРЕПИСОК ПО СКОРОСТИ ОТВЕТА МЕНЕДЖЕРА\n"
            f"(за последние {settings.leaderboard_response_messages_count} сообщений, {now_str})\n\n"
        )
        if leaderboard:
            medals = ["🥇", "🥈", "🥉"]
            for rank, (chat_name, avg_hours) in enumerate(leaderboard, start=1):
                icon = medals[rank - 1] if rank <= 3 else f"{rank}."
                lines.append(f"{icon} {chat_name} — ср. ответ: {format_duration(avg_hours)}\n")
        else:
            lines.append("Недостаточно данных для формирования топа.")
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

    # # Проверка рабочих часов
    # if not is_working_hours(current_time, settings.working_hours):
    #     logger.info(f"Текущее время вне рабочих часов ({settings.working_hours[0]}:00 - {settings.working_hours[1]}:00). Завершение работы.")
    #     return

    logger.info(f"Типы отчётов: {', '.join(settings.report_types)}")
    logger.info(f"Рабочее время. Начинаем проверку чатов...")

    # Создание клиента с сессией
    client = TelegramClient('session', settings.api_id, settings.api_hash)

    try:
        await client.start(phone=settings.phone)
        logger.info("Успешное подключение к Telegram")

        me = await client.get_me()
        logger.info(f"Авторизован как: {me.first_name} (@{me.username})")

        # Получение всех диалогов
        dialogs = await client.get_dialogs()
        logger.info(f"Получено диалогов: {len(dialogs)}")

        need_unread = "unread" in settings.report_types
        need_unanswered = "unanswered" in settings.report_types
        need_leaderboard = "leaderboard" in settings.report_types
        need_chat_analysis = need_unread or need_unanswered

        unread_list: List[Tuple[str, float]] = []
        unanswered_list: List[Tuple[str, float]] = []
        leaderboard_list: List[Tuple[str, float]] = []

        checked_count = 0

        # Анализ каждого диалога
        for dialog in dialogs:
            entity = dialog.entity

            # Пропускаем архивированные чаты
            if dialog.archived:
                logger.debug(f"Пропуск архивированного чата: {format_chat_name(entity)}")
                continue

            # Фильтрация: только личные чаты и группы (исключаем каналы)
            if isinstance(entity, Channel) and entity.broadcast:
                continue

            # Проверка исключений
            chat_id = dialog.id
            chat_username = f"@{entity.username}" if hasattr(entity, 'username') and entity.username else None
            chat_title = entity.title if hasattr(entity, 'title') else None

            if chat_id in settings.excluded_chats or chat_username in settings.excluded_chats or chat_title in settings.excluded_chats:
                logger.debug(f"Пропуск исключенного чата: {format_chat_name(entity)}")
                continue

            if need_leaderboard:
                result = await calculate_chat_avg_response_time(dialog, client, me)
                if result is not None:
                    leaderboard_list.append(result)

            if need_chat_analysis:
                unread_info, unanswered_info = await analyze_chat(dialog, client, current_time)
                if need_unread and unread_info:
                    unread_list.append(unread_info)
                if need_unanswered and unanswered_info:
                    unanswered_list.append(unanswered_info)

            checked_count += 1

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
            logger.info(f"Топ переписок по скорости ответа: {len(top)} из {len(leaderboard_list)}")
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
