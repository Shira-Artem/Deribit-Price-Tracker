# Deribit Price Tracker — этап 3

Python 3.12 backend для разового получения индексных цен `btc_usd` и `eth_usd`
из публичного API Deribit и сохранения в PostgreSQL. Сейчас сбор запускается
только вручную; расписания, фонового worker и API истории ещё нет. HTTP-приложение
предоставляет только `GET /health` с ответом `{"status": "ok"}`. Этот маршрут
проверяет работу FastAPI, а не доступность БД или биржи.

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

Запуск PostgreSQL и миграции:

```powershell
docker compose up -d postgres
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Для уже запущенного PostgreSQL достаточно указать его адрес в `DATABASE_URL` и
выполнить миграцию. Она не запускается при импорте приложения. На отдельной
тестовой базе её можно откатить командой
`.\.venv\Scripts\python.exe -m alembic downgrade base`.

Один ручной цикл получения и сохранения котировок:

```powershell
.\.venv\Scripts\python.exe -m app.collect_once
```

Команда завершается после одного цикла. Она не запускает Celery или сервер.
Итог, полученные цены и ошибки записываются через стандартный `logging` с
уровнем `LOG_LEVEL` из настроек. Если Deribit не ответил по одному тикеру,
второй всё равно может быть сохранён.

Проверка записей в PostgreSQL при стандартных `POSTGRES_USER` и `POSTGRES_DB`:

```powershell
docker compose exec postgres psql -U tracker -d deribit_prices -c "SELECT id, ticker, price, timestamp FROM prices ORDER BY timestamp DESC, id DESC LIMIT 10"
```

HTTP-приложение запускается отдельно:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Проверка: `http://127.0.0.1:8000/health`.

После работы остановите контейнер и Docker Desktop:

```powershell
docker compose down
docker desktop stop
```

`docker compose down` сохраняет именованный том с данными PostgreSQL; при
следующем `docker compose up -d postgres` записи будут доступны. Для обычной
остановки не используйте `down -v`: эта команда удаляет данные. Контейнеру
не задан автоматический перезапуск.

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
`PricePersistenceError`. Закрытие обеих сессий и `Engine.dispose()` остаются
за CLI-точкой входа. Подключений и сетевых запросов при импорте модулей нет.

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
- `app/collect_once.py` — одноразовая CLI-точка входа.
- `app/tasks/` — фабрика Celery; задач пока нет.
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
| Celery Beat + Worker + Redis, ограниченные ретраи и срок действия задач | Запланировано для будущего этапа. |
| API истории с пагинацией | Запланировано; Repository уже принимает `limit` и `offset`. |

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pip check
```

Unit-тесты клиента используют поддельные HTTP-ответы и не обращаются к сети.
Тесты Service и Repository используют изолированную SQLite БД; отдельно
проверяйте ручной цикл на PostgreSQL и публичном API Deribit.
