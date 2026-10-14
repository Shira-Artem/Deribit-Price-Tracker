# Deribit Price Tracker — этап 4

Python 3.12 backend для получения индексных цен `btc_usd` и `eth_usd` из
публичного API Deribit и сохранения в PostgreSQL. Celery Beat публикует задачу
каждую минуту в Redis, а Celery Worker выполняет один цикл сбора. Ручной запуск
цикла также доступен. HTTP-приложение пока предоставляет только `GET /health` с
ответом `{"status": "ok"}`; это проверка FastAPI, а не доступности БД или биржи.

## Локальный запуск

Команды PowerShell выполняются из каталога проекта:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

В `.env` замените `CHOOSE_A_LOCAL_PASSWORD` в `POSTGRES_PASSWORD` и
`DATABASE_URL` на один и тот же выбранный локальный пароль. При смене
`POSTGRES_PORT` поменяйте порт и в `DATABASE_URL`. Файл `.env` игнорируется
Git; `.env.example` не содержит настоящего секрета.

Первый запуск PostgreSQL и миграции:

```powershell
docker compose up -d postgres
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Для уже запущенного PostgreSQL достаточно указать его адрес в `DATABASE_URL` и
выполнить миграцию. Она не запускается при импорте приложения. На отдельной
тестовой базе её можно откатить командой
`.\.venv\Scripts\python.exe -m alembic downgrade base`.

Автоматический сбор после миграции:

```powershell
docker compose up -d --build postgres redis worker beat
docker compose ps
docker compose logs -f worker beat
```

Оставляйте запущенным один экземпляр `beat`: несколько экземпляров создадут
дублирующие задания. Worker и Beat используют один Docker-образ, но запускаются
разными командами. Внутри контейнеров БД доступна по имени `postgres`, Redis —
по имени `redis`; локальная команда PowerShell продолжает использовать
`DATABASE_URL` из `.env`. Redis не публикует порт на домашний ПК. FastAPI пока
запускается локально, отдельно от Compose.

Один ручной цикл получения и сохранения котировок:

```powershell
.\.venv\Scripts\python.exe -m app.collect_once
```

Команда завершается после одного цикла. Она не запускает Celery или сервер.
Автоматический и ручной сбор используют один `collect_once(settings)`. Итог,
полученные цены и ошибки записываются через стандартный `logging`. Если Deribit
не ответил по одному тикеру, второй всё равно может быть сохранён.

Проверка записей в PostgreSQL при стандартных `POSTGRES_USER` и `POSTGRES_DB`:

```powershell
docker compose exec postgres psql -U tracker -d deribit_prices -c "SELECT id, ticker, price, timestamp FROM prices ORDER BY timestamp DESC, id DESC LIMIT 10"
```

HTTP-приложение запускается отдельно:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Проверка: `http://127.0.0.1:8000/health`.

После работы остановите только сервисы этого проекта и Docker Desktop, если он
больше не нужен:

```powershell
docker compose down
docker desktop stop
```

`docker compose down` сохраняет именованный том с данными PostgreSQL; при
следующем запуске записи будут доступны. Для обычной остановки не используйте
`down -v`: эта команда удаляет данные. Сервисам не задан автоматический
перезапуск.

## Движение данных

`app.collect_once` создаёт `Settings`, Engine, sessionmaker, одну
`aiohttp.ClientSession` и одну SQLAlchemy `Session`. `DeribitClient` получает
цены по `GET /api/v2/public/get_index_price?index_name=...` и извлекает только
`result.index_price`. JSON-числа декодируются сразу в `Decimal`; отсутствие
цены, неверный формат, `NaN`, бесконечность, ноль и отрицательные значения
отклоняются.

`PriceService` запрашивает BTC и ETH параллельно и учитывает ошибки независимо.
После завершения обоих HTTP-запросов он один раз берёт UNIX timestamp в
секундах UTC. Успешные цены передаются существующему `PriceRepository`. Если
получены обе цены, они сохраняются в одной транзакции с одинаковым timestamp.
Если получена одна, сохраняется она. Если не получена ни одна, БД не меняется.
Результат цикла содержит сохранённые и неудавшиеся тикеры и timestamp.

Repository только выполняет SQLAlchemy-запросы. Service делает `commit` после
успешной записи и `rollback` при ошибке БД, затем выдаёт отдельную
`PricePersistenceError`. `collect_once(settings)` закрывает HTTP- и DB-сессии,
а также Engine после каждого цикла. Подключений и сетевых запросов при импорте
модулей нет.

## Автоматический сбор

`create_celery_app(settings)` явно регистрирует задачу `prices.collect` и одно
расписание с интервалом 60 секунд. `app.celery_worker:celery_app` — только
точка входа для CLI Celery: при импорте создаются настройки и описание задачи,
но не запускаются Worker, Beat, запросы или подключения к БД. Beat кладёт
задание в Redis, Worker забирает его и вызывает существующий асинхронный
`collect_once(settings)` через `asyncio.run()` в синхронном процессе. Создание
нового event loop при уже работающем loop отклоняется.

Beat задаёт `expires=50` секунд: устаревшее задание не должно исполняться после
долгой очереди. Жёсткий лимит выполнения задачи — 45 секунд, timezone — UTC.
HTTP-клиент сохраняет собственные ограниченные повторы; Celery дополнительных
повторов не делает. При двух неудачных тикерах Celery помечает задачу как
`FAILURE`; при одном успешном — как успех с перечнем неудачных тикеров. Ошибка
БД также приводит к `FAILURE`. В логах Worker указаны task_id, начало и итог.

Worker запускается с `concurrency=1` и выполняет задачи последовательно в
одном экземпляре. Это не гарантирует идемпотентность при запуске нескольких
Worker или повторной доставке сообщения; отдельной блокировки и уникального
ключа котировки пока нет.

## Ошибки и таймауты

На одну HTTP-попытку задан таймаут 5 секунд по умолчанию через
`HTTP_TIMEOUT_SECONDS`. Клиент делает не более трёх попыток: первую и две
повторные, с задержками 0,2 и 0,4 секунды. Повторяются только таймауты,
ошибки соединения, HTTP 429 и HTTP 5xx. HTTP 4xx, JSON-RPC error и неверные
данные не повторяются. `DeribitRequestError` обозначает сбой запроса,
`DeribitResponseError` — неверный ответ; оба наследуют `DeribitClientError`.
Отмена асинхронной задачи не скрывается.

## Схема хранения

Таблица `prices`: `id INTEGER` (автогенерируемый первичный ключ),
`ticker VARCHAR(16) NOT NULL`, `price NUMERIC(20,8) NOT NULL`,
`timestamp BIGINT NOT NULL`. Индекс `ix_prices_ticker_timestamp` по
`(ticker, timestamp)` помогает искать историю отдельного тикера по времени.
При одинаковом timestamp дополнительный ключ сортировки — `id DESC`.
Период истории трактуется как `start_ts <= timestamp < end_ts`.

## Структура

- `app/api/` — маршрут `/health`.
- `app/core/` — конфигурация и фабрика FastAPI.
- `app/infrastructure/deribit_client.py` — HTTP-клиент и проверка ответа.
- `app/infrastructure/database.py` и `models.py` — Engine, сессии и ORM-модель.
- `app/repositories/prices.py` — операции с таблицей `prices`.
- `app/services/price_service.py` — один цикл получения и сохранения.
- `app/collect_once.py` — общий цикл сбора и одноразовая CLI-точка входа.
- `app/tasks/` — фабрика Celery, задача и расписание.
- `app/celery_worker.py` — импортируемая CLI-точка входа Celery.
- `Dockerfile` и `compose.yaml` — общий образ Worker/Beat и сервисы PostgreSQL, Redis, Worker, Beat.
- `alembic/` — версия схемы БД.
- `tests/` — тесты клиента, сервиса, Repository и `/health`.

## Design decisions

| Решение | Обоснование и состояние |
| --- | --- |
| `Decimal` и PostgreSQL `NUMERIC(20,8)` вместо `Float` | Сохраняют десятичную точность цены. Реализовано. |
| UNIX timestamp UTC в `BIGINT` вместо `DateTime` | Единый формат без преобразований часовых поясов и ограничений 32-битного `INTEGER`. Реализовано. |
| Один timestamp после обоих HTTP-запросов | Цены одного цикла имеют общую отметку времени. Реализовано. |
| Независимые результаты BTC и ETH | Сбой одного запроса не теряет удачную котировку второго. Реализовано. |
| Одна HTTP-сессия на цикл | Соединения переиспользуются и гарантированно закрываются. Реализовано. |
| Синхронный SQLAlchemy после асинхронных HTTP-запросов | Короткая запись выполняется после `gather`; она не блокирует параллельные HTTP-корутины. Реализовано. |
| `PriceRepository` с переданной `Session` | Отделяет SQL-запросы от логики сбора. Реализовано. |
| Транзакция на уровне Service | Успешные цены одного цикла сохраняются атомарно, при ошибке записи выполняется rollback. Реализовано. |
| Индекс `(ticker, timestamp)` | Ускоряет выборку по тикеру и времени. Реализовано. |
| Alembic вместо автоматического `create_all()` | Даёт управляемые upgrade и downgrade. Реализовано. |
| Нет `UNIQUE(ticker, timestamp)` | Два измерения могут попасть в одну секунду; идемпотентность будущих задач рассмотрим отдельно. |
| Нет отдельной domain-сущности | Пока ORM-модели достаточно. |
| Celery Beat + Worker + Redis | Beat раз в 60 секунд публикует одну задачу, Worker её исполняет. Реализовано. |
| `expires=50`, hard time limit 45 секунд, UTC | Снижают риск исполнения устаревшего задания и зависания цикла. Реализовано. |
| Worker `concurrency=1` | Один Worker обрабатывает задания последовательно. Не защищает от нескольких Worker или повторной доставки. Реализовано с этим ограничением. |
| HTTP retries без Celery retries | Повторяются только временные ошибки HTTP внутри клиента; Celery не создаёт дополнительные циклы при сбое. Реализовано. |
| API истории с пагинацией | Запланировано; Repository уже принимает `limit` и `offset`. |

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pip check
```

Unit-тесты клиента и Celery не обращаются к сети или брокеру. Тесты Service и
Repository используют изолированную SQLite БД. Реальную работу расписания
проверяйте через Compose, логи и строки PostgreSQL.
