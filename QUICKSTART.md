# Быстрый старт

## Шаг 1: Установка зависимостей

```bash
pip install -r requirements.txt
```

## Шаг 2: Получение Telegram API credentials

1. Откройте https://my.telegram.org/auth
2. Войдите с номером телефона
3. Перейдите в "API development tools"
4. Создайте приложение и скопируйте **API ID** и **API Hash**

## Шаг 3: Настройка скрипта

Откройте `telegram_monitor.py` и замените:

```python
API_ID = 12345  # Ваш реальный API ID
API_HASH = "your_api_hash_here"  # Ваш реальный API Hash
PHONE = "+79991234567"  # Номер телефона аккаунта
```

Опционально настройте:
- `REPORT_TO` - кому отправлять сводку (по умолчанию @victorryakh)
- `WORKING_HOURS` - рабочие часы (по умолчанию 9-21)
- `RESPONSE_TIMEOUT_HOURS` - допустимое время без ответа (по умолчанию 2 часа)
- `EXCLUDED_CHATS` - список исключаемых чатов

## Шаг 4: Первый запуск

```bash
python telegram_monitor.py
```

При первом запуске введите код из Telegram для авторизации.

## Шаг 5: Настройка автозапуска (опционально)

```bash
crontab -e
```

Добавьте строку для запуска каждый час в рабочее время:

```
0 9-21 * * * cd /home/vryakhovskiy/Documents/git/victorryakh/telegram-police && /usr/bin/python3 telegram_monitor.py
```

**Важно:** Замените пути на актуальные для вашей системы:
- Путь к проекту: `pwd`
- Путь к Python: `which python3`

## Готово! 🎉

Скрипт будет:
- ✅ Проверять чаты только в рабочее время
- ✅ Находить непрочитанные и неотвеченные сообщения
- ✅ Учитывать реакции как ответы
- ✅ Отправлять сводку указанному пользователю
- ✅ Вести подробные логи в `telegram_monitor.log`

## Проверка работы

Просмотр логов:
```bash
tail -f telegram_monitor.log
```

Для подробной документации см. [README.md](README.md)
