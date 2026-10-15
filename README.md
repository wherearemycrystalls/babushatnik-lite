# Babushatnik Lite

Лёгкий сервис поиска арендных объявлений Kufar: скрейпер поддерживает актуальный
каталог, а Telegram-бот автоматически присылает новые квартиры по критериям
пользователя. Можно создать несколько наборов критериев, включать нужные
и видеть все совпавшие наборы в карточке. Подходящие карточки можно сохранить в избранное; снятые объявления
переходят в его архив.

Python и зависимости управляются через [uv](https://docs.astral.sh/uv/).
Для подготовки проекта и проверки:

```bash
brew install uv  # macOS; если uv ещё не установлен
make install
make check
```

`make install` устанавливает Python из `.python-version` и зависимости из
`uv.lock`. uv сам создаёт локальную `.venv`; активация окружения не требуется.
Команды запуска: `make admin`, `make bot`, `make worker-scraper`,
`make worker-bot`, `make beat` (нужны доступные PostgreSQL и Redis).

Полный локальный Docker-стек: `make local-up`; состояние: `make local-ps`;
логи: `make local-logs`; остановка с сохранением данных: `make local-down`.
Конфигурация окружений и production-запуск описаны в [docs/COMPOSE.md](docs/COMPOSE.md).

Архитектура, модель активности объявлений и правила восстановления описаны в
[docs/LISTINGS_PIPELINE.md](docs/LISTINGS_PIPELINE.md).

Сценарий поиска, доставки карточек и избранное описаны в
[docs/BOT_SEARCH.md](docs/BOT_SEARCH.md).

Поля Kufar, даты, история изменений и правила новизны описаны в
[docs/SOURCE_DATA.md](docs/SOURCE_DATA.md).

Разработка, проверка и безопасный production-релиз описаны в
[CONTRIBUTING.md](CONTRIBUTING.md) и [docs/CICD.md](docs/CICD.md).
