# Telegram PC Control MTProto

Windows-приложение с иконкой в трее и Telegram-ботом для удаленного управления своим компьютером через локальный MTProto-прокси.

Что умеет:

- автозапуск вместе с Windows
- скрытый запуск в фоне
- доступ только для одного `allowed_user_id`
- красивое главное меню со статусом ПК
- активные приложения и окна с мягким закрытием
- скриншот выбранного окна по кнопке
- кнопка `Свернуть все приложения`
- меню `Запуск приложений`
- редактор приложений и паков без ручного редактирования JSON

## Что нужно заранее

- Windows 10 или Windows 11
- Python 3.10+ с установленным `py` launcher
- Telegram-аккаунт
- токен бота от `@BotFather`
- `api_id` и `api_hash` от Telegram
- локальный MTProto-прокси

## 1. Скачать проект

После публикации на GitHub можно:

1. Нажать `Code` → `Download ZIP`
2. Или клонировать репозиторий через `git clone`

Распакуйте проект в удобную папку, например `C:\TelegramPcControl`.

## 2. Установить локальный MTProto-прокси

Этот проект рассчитан на локальный MTProto-прокси. В качестве примера используется `tg-ws-proxy`:

- репозиторий: [Flowseal/tg-ws-proxy](https://github.com/Flowseal/tg-ws-proxy)
- релизы: [tg-ws-proxy releases](https://github.com/Flowseal/tg-ws-proxy/releases)

По README проекта `tg-ws-proxy` на Windows:

1. Откройте страницу релизов.
2. Скачайте `TgWsProxy_windows.exe`.
3. Запустите его.
4. Приложение свернется в системный трей.

Дальше есть два удобных варианта:

1. ПКМ по иконке в трее → `Скопировать ссылку`
2. Или ПКМ по иконке в трее → `Открыть в Telegram`

Для этого проекта удобнее всего взять ссылку формата:

```text
https://t.me/proxy?server=127.0.0.1&port=1443&secret=...
```

и вставить ее потом в `config.json` в поле `telegram_proxy`.

Если ссылка недоступна, можно настроить вручную. По README `tg-ws-proxy` по умолчанию он поднимает MTProto-прокси на:

- сервер: `127.0.0.1`
- порт: `1443`
- `secret`: берется из настроек или логов самого `tg-ws-proxy`

## 3. Получить токен бота

Официальный гайд Telegram: [BotFather guide](https://core.telegram.org/bots/features)

Коротко:

1. Откройте в Telegram `@BotFather`
2. Отправьте команду `/newbot`
3. Укажите имя бота
4. Укажите username бота, который должен заканчиваться на `bot`
5. Сохраните выданный токен

Этот токен потом нужно вставить в `config.json` в поле `telegram_token`.

## 4. Получить `api_id` и `api_hash`

Официальная инструкция Telegram: [Obtaining api_id](https://core.telegram.org/api/obtaining_api_id)

Коротко:

1. Откройте [my.telegram.org](https://my.telegram.org)
2. Войдите по своему Telegram-аккаунту
3. Зайдите в `API development tools`
4. Заполните форму
5. Сохраните `api_id` и `api_hash`

Их нужно вставить в `config.json` в поля:

- `telegram_api_id`
- `telegram_api_hash`

## 5. Узнать свой `allowed_user_id`

Самый простой способ:

1. Откройте в Telegram `@userinfobot`
2. Отправьте ему любое сообщение
3. Скопируйте свой числовой `user_id`

Его нужно вставить в `config.json` в поле `allowed_user_id`.

## 6. Установить зависимости

Откройте PowerShell в папке проекта.

Обычный запуск:

```powershell
.\setup.ps1
```

Если PowerShell ругается на запрет выполнения скриптов, запускайте так:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

Либо временно для текущего окна:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

Что делает `setup.ps1`:

- создает `.venv`
- ставит зависимости из `requirements.txt`
- копирует `config.example.json` в `config.json`, если его еще нет
- копирует `launch_catalog.example.json` в `launch_catalog.json`, если его еще нет

## 7. Заполнить `config.json`

После `setup.ps1` рядом появится файл `config.json`.

Пример:

```json
{
  "telegram_token": "1234567890:PASTE_YOUR_TOKEN_HERE",
  "allowed_user_id": 123456789,
  "telegram_api_id": 123456,
  "telegram_api_hash": "0123456789abcdef0123456789abcdef",
  "telegram_proxy": "https://t.me/proxy?server=127.0.0.1&port=1443&secret=PASTE_YOUR_MTPROTO_SECRET_HERE"
}
```

Важно:

- не публикуйте свой реальный `config.json`
- не публикуйте `telegram_token`
- не публикуйте `api_hash`

## 8. Добавить приложения и паки

Есть два способа.

Самый удобный:

- запустите `open_launch_catalog_editor.vbs`

или после старта бота:

- откройте пункт `Редактор запусков` в трее

В редакторе можно:

- добавить одиночное приложение
- указать путь к `.exe`, `.lnk`, папке или ссылке
- задать аргументы запуска
- задать рабочую папку
- задать режим окна
- собрать пак из нескольких приложений

Если хотите редактировать руками, рабочий файл:

- `launch_catalog.json`

Пример структуры:

```json
{
  "apps": [
    {
      "id": "telegram",
      "title": "Telegram",
      "target": "C:\\Program Files\\Telegram Desktop\\Telegram.exe",
      "arguments": "",
      "start_in": "",
      "window_style": "normal"
    },
    {
      "id": "browser",
      "title": "Yandex Browser",
      "target": "C:\\Users\\User\\AppData\\Local\\Yandex\\YandexBrowser\\Application\\browser.exe",
      "arguments": "",
      "start_in": "",
      "window_style": "normal"
    }
  ],
  "packs": [
    {
      "id": "work",
      "title": "Работа",
      "apps": ["browser", "telegram"],
      "delay_ms": 700
    }
  ]
}
```

Если редактируете JSON руками, Windows-пути обязательно должны быть с двойными слэшами:

```json
"target": "C:\\Users\\User\\AppData\\Local\\Programs\\App\\app.exe"
```

Именно из-за этого возникает ошибка вида `invalid escape`.

## 9. Запустить бота

Обычный запуск:

```powershell
.\start_bot.ps1
```

Если PowerShell блокирует запуск:

```powershell
powershell -ExecutionPolicy Bypass -File .\start_bot.ps1
```

После запуска:

- бот стартует в фоне
- появляется иконка в системном трее
- программа прописывает себя в автозапуск

Потом напишите вашему боту в Telegram:

```text
/start
```

## 10. Что есть в трее

В трее доступны пункты:

- `Открыть config.json`
- `Редактор запусков`
- `Открыть launch_catalog.json`
- `Перезапустить бота`
- `Выход`

## Полезные файлы

- `telegram_pc_bot.py` — основной бот
- `launch_catalog_editor.py` — редактор приложений и паков
- `open_launch_catalog_editor.vbs` — скрытый запуск редактора без консоли
- `setup.ps1` — установка зависимостей
- `start_bot.ps1` — запуск бота
- `config.example.json` — шаблон конфига
- `launch_catalog.example.json` — шаблон каталога запусков
- `telegram_pc_bot.log` — лог работы

## Что не нужно коммитить в Git

Уже добавлено в `.gitignore`:

- `.venv/`
- `config.json`
- `launch_catalog.json`
- `telegram_pc_bot.log`
- `mtproto_bot.session`

## Быстрая проверка

```powershell
.\.venv\Scripts\python.exe .\telegram_pc_bot.py --self-test
```

## Источники

- [tg-ws-proxy repository](https://github.com/Flowseal/tg-ws-proxy)
- [tg-ws-proxy releases](https://github.com/Flowseal/tg-ws-proxy/releases)
- [Telegram BotFather guide](https://core.telegram.org/bots/features)
- [Telegram API ID / API hash guide](https://core.telegram.org/api/obtaining_api_id)
