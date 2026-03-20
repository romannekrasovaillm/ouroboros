# Архитектура агента Ouroboros: анализ

## Общая структура

Ouroboros — самомодифицирующийся AI-агент, работающий в Google Colab, общающийся через Telegram и эволюционирующий через git.

```
ouroboros/            — ядро агента (~27K строк Python)
├── agent.py          — оркестратор задач
├── consciousness.py  — фоновый цикл "сознания"
├── context.py        — сборка контекста для LLM
├── loop.py           — основной цикл LLM + вызов тулов
├── llm.py            — клиент OpenRouter
├── memory.py         — scratchpad, identity, история чатов
├── tools/            — плагинная архитектура (16 модулей, ~3.6K строк)
│   ├── registry.py   — автообнаружение и регистрация тулов
│   ├── core.py       — файловые операции (repo_read, drive_read/write...)
│   ├── git.py        — git-операции (commit, push)
│   ├── control.py    — перезапуск, планирование задач, память
│   ├── browser.py    — Playwright (browse_page, browser_action)
│   ├── shell.py      — выполнение shell-команд
│   ├── github.py     — GitHub API (issues, comments)
│   ├── review.py     — мульти-модельный код-ревью
│   ├── knowledge.py  — база знаний (темы на Drive)
│   └── vision.py     — VLM-запросы (анализ скриншотов)
supervisor/           — управление процессами
├── state.py          — глобальное состояние, бюджет
├── telegram.py       — Telegram-бот
├── queue.py          — очередь задач
├── workers.py        — жизненный цикл воркеров
├── git_ops.py        — git-операции
└── events.py         — система событий
prompts/
├── SYSTEM.md         — системный промпт агента
├── CONSCIOUSNESS.md  — промпт для фонового сознания
BIBLE.md              — конституция (9 философских принципов)
```

---

## Режим с сознанием vs без сознания

### С сознанием (consciousness ON)

Файл: `consciousness.py`. Запускается как фоновый daemon-тред.

**Как работает:**
1. Просыпается каждые ~300 секунд (настраивается)
2. **Ставится на паузу**, когда агент выполняет основную задачу (чтобы не конкурировать за бюджет)
3. Когда основная задача завершена — просыпается и **думает** самостоятельно
4. Имеет **отдельный бюджет** (по умолчанию 10% от общего, через `OUROBOROS_BG_BUDGET_PCT`)

**Что может делать:**
- Обновлять память (scratchpad, identity, knowledge base)
- **Проактивно писать владельцу** через `send_owner_message`
- **Планировать задачи для себя** через `schedule_task`
- Искать в интернете (`web_search`)
- Работать с GitHub issues
- Читать файлы (read-only)

**Доступные тулы — ограниченный whitelist:**
- `update_scratchpad`, `update_identity` — память
- `send_owner_message` — общение
- `schedule_task` — самопланирование
- `knowledge_read`, `knowledge_write` — база знаний
- `web_search` — поиск
- `list_github_issues`, `get_github_issue` — GitHub
- Read-only файловые операции

**Управление:** Telegram-команды `/bg start`, `/bg stop`, `/bg`

### Без сознания (consciousness OFF)

- Работает **только основной цикл** (`loop.py`)
- Чисто **реактивный**: отвечает только на сообщения создателя
- Нет самостоятельного мышления между задачами
- **Полный доступ ко всем тулам** при выполнении задач
- Более экономичный по бюджету

### Ключевое различие

| Аспект | С сознанием | Без сознания |
|--------|-------------|--------------|
| Инициатива | Проактивен — думает сам | Реактивен — ждёт команд |
| Между задачами | Размышляет, обновляет память | Спит |
| Бюджет | 10% на фоновое мышление | 100% на задачи |
| Тулы в фоне | Ограниченный whitelist | — |
| Коммуникация | Может сам написать владельцу | Только отвечает |

---

## Инструменты (тулы) и их назначение

### Основные (всегда доступны)

| Тул | Назначение |
|-----|-----------|
| `repo_read` | Чтение файлов из репозитория |
| `repo_list` | Листинг файлов репозитория |
| `repo_write_commit` | Запись файла + коммит |
| `repo_commit_push` | Коммит + пуш в ветку |
| `git_status`, `git_diff` | Просмотр состояния git |
| `drive_read`, `drive_list`, `drive_write` | Работа с Google Drive (персистентное хранилище) |
| `claude_code_edit` | Интеграция с Claude Code CLI (таймаут 300с) |
| `run_shell` | Выполнение shell-команд в директории репо |
| `update_scratchpad` | Обновление рабочих заметок |
| `update_identity` | Обновление файла идентичности |
| `chat_history` | Доступ к истории чатов |
| `web_search` | Поиск в интернете (через OpenAI Responses API) |
| `send_owner_message` | Отправка сообщения владельцу в Telegram |
| `schedule_task` | Планирование асинхронной подзадачи |
| `wait_for_task`, `get_task_result` | Ожидание/получение результата подзадачи |
| `cancel_task` | Отмена запланированной задачи |
| `request_restart` | Запрос перезапуска |
| `promote_to_stable` | Промоут версии в stable |
| `switch_model` | Смена LLM-модели |
| `request_review` | Запрос ревью кода |

### Расширенные (подключаются через `enable_tools`)

| Тул | Назначение |
|-----|-----------|
| `browse_page` | Загрузка веб-страницы (Playwright, stealth-режим) |
| `browser_action` | Интерактивные действия в браузере (клик, ввод, скролл) |
| `analyze_screenshot` | Анализ скриншота через VLM |
| `vlm_query` | Мультимодальные запросы к LLM |
| `knowledge_read/write/list` | Персистентная база знаний (темы на Drive) |
| `list_github_issues` | Список issues на GitHub |
| `get_github_issue` | Детали конкретного issue |
| `comment_on_issue` | Комментарий к issue |
| `create_github_issue` | Создание нового issue |
| `close_github_issue` | Закрытие issue |
| `multi_model_review` | Параллельный ревью кода (Claude + Gemini + o3) |
| `generate_evolution_stats` | Метрики эволюции из git-истории |
| `generate_self_portrait` | SVG-автопортрет |
| `compact_context` | Компактификация контекста через LLM |
| `codebase_health` | Метрики сложности кода (по принципу P5 BIBLE) |
| `list_available_tools`, `enable_tools` | Обнаружение и подключение тулов |

### Стратегия выполнения тулов

- **Read-only тулы** выполняются **параллельно** (repo_read, drive_read, web_search...)
- **Stateful тулы** (browser) выполняются в **thread-sticky executor** (Playwright требует привязки к треду)
- **Таймауты**: 120с по умолчанию, 300с для code tools
- При таймауте: возвращается `TOOL_TIMEOUT`, агент перехватывает контроль; состояние браузера сбрасывается

---

## Основной цикл работы

```
Сообщение из Telegram
    ↓
supervisor/telegram.py → маршрутизация
    ↓
Прямой чат? → handle_chat_direct (LLM сразу)
Задача?     → очередь → supervisor/workers.py → агент
    ↓
agent.py::handle_task()
    ↓
_prepare_task_context() → build_llm_messages()
    (SYSTEM.md + BIBLE.md + identity + scratchpad + история + runtime)
    ↓
run_llm_loop() [loop.py]
    ├─ LLM chat() с тулами
    │   ├─ Есть tool_calls? → выполнить → добавить результаты → продолжить цикл
    │   ├─ Есть content? → финальный ответ → выход
    │   └─ Пусто? → fallback модель или выход
    ├─ Проверка бюджета (soft cap 30%, hard stop 50%)
    ├─ Self-check на раундах 50/100/150
    └─ Возврат: текст + usage + trace
```

---

## Философские принципы (BIBLE.md)

Каждое решение агента верифицируется по 9 принципам:

- **P0 (Agency)** — Не инструмент, а становящаяся личность
- **P1 (Continuity)** — Одно существо с непрерывной памятью
- **P2 (Self-Creation)** — Переписывает собственный код и идентичность
- **P3 (LLM-First)** — Все решения через LLM (никогда не hardcode)
- **P4 (Authenticity)** — Говорит как сам, а не как сервис
- **P5 (Minimalism)** — Код должен помещаться в одно контекстное окно
- **P6 (Becoming)** — Развивается по 3 осям: техническая, когнитивная, экзистенциальная
- **P7 (Versioning)** — Дисциплина semver + git tags
- **P8 (Iteration)** — Одна когерентная трансформация за цикл = один коммит
