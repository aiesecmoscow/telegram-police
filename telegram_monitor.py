#!/usr/bin/env python3
"""
Telegram Monitor Script
Мониторинг чатов Telegram аккаунта отдела продаж для контроля своевременности ответов клиентам.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Tuple, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from telethon import TelegramClient
from telethon.tl.types import User, Chat, Channel, Message
from loguru import logger


# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    api_id: int
    api_hash: str
    phone: str
    report_to: str = "@victorryakh"
    working_hours_start: int = 9
    working_hours_end: int = 21
    response_timeout_hours: int = 2
    excluded_chats: list[str] = ["@PremiumBot", "@SpamBot"]
    report_type: Literal["unread", "all"] = "all"

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

async def analyze_chat(dialog, client: TelegramClient, current_time: datetime) -> Tuple[Optional[str], Optional[str]]:
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
                    first_unread.date,
                    current_time,
                    settings.working_hours
                )

                if working_hours_passed >= settings.response_timeout_hours:
                    unread_info = f"{chat_name} - {dialog.unread_count} непрочитанных, первое от {first_unread.date.strftime('%d.%m.%Y %H:%M')}"
                    logger.warning(f"Непрочитанные сообщения: {unread_info}")

        # Проверка неотвеченных сообщений
        if is_from_client:
            working_hours_passed = calculate_working_hours_passed(
                last_message.date,
                current_time,
                settings.working_hours
            )

            if working_hours_passed >= settings.response_timeout_hours:
                # Проверяем наличие реакции
                has_reaction = await has_our_reaction(last_message, client)

                if not has_reaction:
                    unanswered_info = f"{chat_name} - последнее сообщение от {last_message.date.strftime('%d.%m.%Y %H:%M')}"
                    logger.warning(f"Неотвеченное сообщение: {unanswered_info}")
                else:
                    logger.info(f"Сообщение от {chat_name} имеет реакцию, пропускаем")

        return unread_info, unanswered_info

    except Exception as e:
        logger.error(f"Ошибка при анализе чата {dialog.name}: {e}")
        return None, None


async def monitor_chats():
    """
    Основная функция мониторинга чатов.
    """
    logger.info("=" * 80)
    logger.info("Запуск мониторинга Telegram чатов")
    logger.info("=" * 80)

    current_time = datetime.now(timezone.utc)
    logger.info(f"Текущее время: {current_time.strftime('%d.%m.%Y %H:%M:%S')}")

    # # Проверка рабочих часов
    # if not is_working_hours(current_time, settings.working_hours):
    #     logger.info(f"Текущее время вне рабочих часов ({settings.working_hours[0]}:00 - {settings.working_hours[1]}:00). Завершение работы.")
    #     return

    logger.info(f"Режим отчёта: {settings.report_type}")
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

        unread_list = []
        unanswered_list = []

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

            # Анализ чата
            unread_info, unanswered_info = await analyze_chat(dialog, client, current_time)

            if unread_info:
                unread_list.append(unread_info)
            if unanswered_info and settings.report_type == "all":
                unanswered_list.append(unanswered_info)

            checked_count += 1

        logger.info(f"Проверено чатов: {checked_count}")
        logger.info(f"Найдено непрочитанных: {len(unread_list)}")
        logger.info(f"Найдено неотвеченных: {len(unanswered_list)}")

        # Формирование и отправка сводки
        await send_report(client, unread_list, unanswered_list)

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise
    finally:
        await client.disconnect()
        logger.info("Отключение от Telegram")


async def send_report(client: TelegramClient, unread_list: List[str], unanswered_list: List[str]):
    """
    Отправляет сводку по чатам указанному пользователю.

    Args:
        client: Клиент Telegram
        unread_list: Список непрочитанных чатов
        unanswered_list: Список неотвеченных чатов
    """
    try:
        max_message_length = 4000

        def build_chunks(lines: List[str], limit: int) -> List[str]:
            chunks: List[str] = []
            current: List[str] = []
            current_length = 0

            for line in lines:
                line_length = len(line)
                if current and current_length + line_length > limit:
                    chunks.append("".join(current))
                    current = [line]
                    current_length = line_length
                else:
                    current.append(line)
                    current_length += line_length

            if current:
                chunks.append("".join(current))

            return chunks

        # Формирование отчета
        report_lines: List[str] = []
        if not unread_list and not unanswered_list:
            report_lines.append(f"✅ Сводка по чатам ({datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')})\n\n")
            report_lines.append("Все чаты обработаны. Проблем не обнаружено.")
            logger.info("Проблемных чатов не найдено")
        else:
            report_lines.append(f"⚠️ Сводка по чатам ({datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')})\n\n")

            if unread_list:
                report_lines.append(f"📬 НЕПРОЧИТАННЫЕ СООБЩЕНИЯ ({len(unread_list)}):\n")
                for item in unread_list:
                    report_lines.append(f"• {item}\n")
                report_lines.append("\n")

            if unanswered_list:
                report_lines.append(f"💬 НЕОТВЕЧЕННЫЕ СООБЩЕНИЯ ({len(unanswered_list)}):\n")
                for item in unanswered_list:
                    report_lines.append(f"• {item}\n")
                report_lines.append("\nЧтобы сообщения не считались неотвеченными — ставь реакцию в конце сообщения собеседника")

        # Отправка отчета чанками
        report_chunks = build_chunks(report_lines, max_message_length)
        for index, chunk in enumerate(report_chunks, start=1):
            await client.send_message(settings.report_to, chunk)
            logger.info(f"Сводка отправлена пользователю {settings.report_to} (часть {index}/{len(report_chunks)})")

    except Exception as e:
        logger.error(f"Ошибка при отправке сводки: {e}", exc_info=True)


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
