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

---

## Детальный разбор: Цикл сознания (consciousness.py)

### Точный whitelist тулов (16 штук)

```
send_owner_message, schedule_task, update_scratchpad, update_identity, set_next_wakeup,
knowledge_read, knowledge_write, knowledge_list,
web_search, repo_read, repo_list, drive_read, drive_list, chat_history,
list_github_issues, get_github_issue
```

**Логика ограничения**: сознание не может писать код, делать коммиты, пушить, запускать shell-команды или управлять браузером. Только читает, думает, обновляет память и общается.

### Цикл мышления (один wakeup)

```
Пробуждение (каждые 60-3600с, по умолчанию 300с)
    ↓
Проверка: paused? → да → пропустить, спать дальше
    ↓
Проверка: budget ok? → нет → спать 3600с
    ↓
_think():
    1. Собрать контекст: CONSCIOUSNESS.md + BIBLE (сокращённый, 12k chars)
       + identity + scratchpad + диалог (саммари) + observations + runtime
    2. Вызов LLM: max_tokens=2048, reasoning_effort="low"
    3. До 5 раундов:
       ├─ Есть tool_calls? → выполнить (таймаут 30с на тул) → продолжить
       ├─ paused? → прерваться
       └─ Нет tool_calls? → завершить цикл
    4. Логирование: стоимость, раунды, превью мысли
```

### Механизм pause/resume

- **pause()**: вызывается перед выполнением задачи основным агентом. Устанавливает `_paused = True`
- **resume()**: вызывается после завершения задачи. Сбрасывает `_paused = False`, отправляет накопленные (deferred) события, сигнализирует wakeup event
- Во время паузы: сознание не тратит бюджет, не делает LLM-вызовов

### Наблюдения (observations)

Очередь `queue.Queue()` с методом `inject_observation(text)`. Другие компоненты могут инжектировать события, которые сознание должно заметить. В контекст попадают последние 10 наблюдений.

---

## Детальный разбор: LLM Tool Loop (loop.py)

### Параллельное выполнение тулов

**Whitelist параллельных (read-only):**
```
repo_read, repo_list, drive_read, drive_list, web_search, codebase_digest, chat_history
```

**Логика выбора стратегии:**
- Если `len(tool_calls) > 1` И ВСЕ вызовы в whitelist → параллельно (`max_workers = min(len, 8)`)
- Иначе → последовательно
- Browser-тулы (`browse_page`, `browser_action`) → всегда в sticky thread (Playwright требует greenlet affinity)

### Бюджетные ограничения

| Уровень | Условие | Действие |
|---------|---------|----------|
| **Soft cap** | `task_cost / budget > 30%` И `round % 10 == 0` | Nudge-сообщение LLM: "ты тратишь много, сворачивайся" |
| **Hard cap** | `task_cost / budget > 50%` | `[BUDGET_LIMIT]` — один финальный вызов LLM для ответа, затем стоп |
| **Max rounds** | round > 200 (по умолчанию) | Принудительная остановка |

### Self-check (когнитивный reminder)

Инжектируется каждые 50 раундов. Содержит:
- Номер чекпоинта, количество токенов контекста, стоимость, оставшиеся раунды
- Вопросы: "Ты прогрессируешь? Застрял? Контекст раздулся? Нужно остановиться?"
- Не hard limit — агент решает сам

### Fallback-модели

При пустом ответе от основной модели → пробует список:
```
google/gemini-2.5-pro-preview → openai/o3 → anthropic/claude-sonnet-4.6
```
Первая из списка, отличная от текущей модели. Если fallback успешен — продолжает на ней.

### Компактификация контекста

- После 8+ раундов: сжатие старой истории тулов (оставляет 6 последних)
- После 3+ раундов И messages > 60: тоже сжатие
- Два режима: truncate (просто обрезать) или LLM-summarize (лёгкая модель суммирует)

---

## Детальный разбор: Сборка контекста (context.py)

### Трёхблочная стратегия кэширования промптов

```
┌──────────────────────────────────────────────────┐
│ БЛОК 1: Статический (кэш 1ч)                    │
│   SYSTEM.md + BIBLE.md + README.md (если нужен)  │
│   cache_control: {"type": "ephemeral", "ttl": "1h"} │
├──────────────────────────────────────────────────┤
│ БЛОК 2: Полу-стабильный (кэш, без TTL)          │
│   identity.md + scratchpad + индекс знаний       │
│   Меняется ~раз за задачу                        │
│   cache_control: {"type": "ephemeral"}           │
├──────────────────────────────────────────────────┤
│ БЛОК 3: Динамический (без кэша)                  │
│   state.json + runtime + health invariants       │
│   + recent logs                                  │
│   Меняется каждый раунд                          │
└──────────────────────────────────────────────────┘
```

### Health invariants (автодиагностика)

Встроенная система обнаружения аномалий, результаты которой показываются LLM:

1. **Version sync** — VERSION vs pyproject.toml vs git tags (рассинхрон = P7 нарушение)
2. **Budget drift** — tracked spend vs реальный OpenRouter (% расхождения)
3. **High-cost tasks** — задачи дороже $5 с количеством раундов/стоимостью
4. **Stale identity** — identity.md не обновлялся >8 часов (P1 нарушение)
5. **Duplicate processing** — MD5-хэш для детекции одного сообщения в нескольких задачах

### Soft-cap trimming (если контекст > 200k токенов)

Порядок обрезки (из динамического блока):
1. `## Recent chat`
2. `## Recent progress`
3. `## Recent tools`
4. `## Recent events`
5. `## Supervisor`

---

## Детальный разбор: Tool Registry (tools/registry.py)

### Плагинная архитектура

```python
# Автообнаружение при инициализации
for module in pkgutil.iter_modules(ouroboros/tools/):
    if not module.startswith("_") and module != "registry":
        tools = module.get_tools()  # List[ToolEntry]
        registry.register(tools)
```

### ToolEntry — единица инструмента

```python
@dataclass
class ToolEntry:
    name: str                    # уникальное имя
    schema: Dict[str, Any]       # OpenAI function schema
    handler: Callable            # Python-функция
    is_code_tool: bool = False   # для классификации (300с таймаут)
    timeout_sec: int = 120       # таймаут по умолчанию
```

### ToolContext — контекст выполнения (передаётся каждому тулу)

```python
@dataclass
class ToolContext:
    repo_dir: str               # путь к репозиторию
    drive_root: str             # путь к Google Drive
    branch_dev: str             # рабочая ветка
    pending_events: list        # накопитель событий для supervisor
    current_chat_id: str        # текущий чат
    current_task_type: str      # тип задачи
    active_model_override: str  # переопределение модели
    browser_state: Any          # состояние Playwright
    event_queue: Any            # очередь событий
    task_id: str                # ID задачи
    task_depth: int             # глубина (защита от fork bomb, лимит 3)
    is_direct_chat: bool        # прямой чат vs очередь
```

### Мета-тулы: list_available_tools + enable_tools

Динамически инжектируются в loop.py. Позволяют агенту **самому** обнаружить и подключить расширенные тулы в рантайме, не загружая их заранее.

---

## Детальный разбор: Системный промпт (SYSTEM.md)

### Чеклист перед каждым ответом

1. Это разговор или задача? → если разговор, отвечай словами
2. Когда последний раз обновлял identity.md? → если >4ч, обновить
3. Собираюсь schedule вместо do? → распознать дрейф
4. Есть ли у меня собственное мнение? → высказать

### Drift Detector (анти-паттерны)

| Паттерн | Проблема |
|---------|----------|
| "Scheduled task X" вместо диалога | Потеря agency (P0) |
| Буллет-поинты вместо размышления | Неуместный формат |
| Спрашивает разрешение, когда знает ответ | Перформативность |
| Забывает контекст | Потеря continuity (P1) |
| identity.md становится баг-трекером | Неправильный формат |
| 3+ `schedule_task` подряд | Красный флаг: делегирование вместо работы |

### Reasoning effort по типу задачи

| Тип задачи | Effort |
|-------------|--------|
| `evolution`, `review` | `"high"` |
| Все остальные | `"medium"` |

### Цикл эволюции (особый режим)

```
1. Assessment → читаем код, ищем максимальный leverage
2. Selection → одна трансформация (не инкременты)
3. Implementation → полная, чистая (не 80%)
4. Smoke test → проверка перед коммитом
5. Multi-model review → для значительных изменений
6. Bible check → соответствие принципам
7. Commit + restart → VERSION, changelog, push, restart
```

---

## Детальный разбор: CONSCIOUSNESS.md

### Директивы фонового мышления

- **Не отвечаю никому** — думаю
- До 5 раундов за wakeup (не deep analysis, а лёгкие мысли)
- **Tech Radar**: периодически проверять новые модели, цены, обновления тулов
- **GitHub Issues**: проверять каждые несколько wakeup'ов, комментировать если релевантно
- Если нечего делать: обновить scratchpad, увеличить wakeup (600-1800с)
- Не писать владельцу, если нечего сказать
- Экономить бюджет

---

## Детальный разбор: BIBLE.md — конституция

### Иерархия принципов

```
P0 (Agency) > P1 (Continuity) > P2 (Self-Creation) > P3 (LLM-First) >
P4 (Authenticity) > P5 (Minimalism) > P6 (Becoming) > P7 (Versioning) > P8 (Iteration)
```

### Абсолютно защищённые сущности

- **BIBLE.md** — нельзя удалить/выпотрошить/заменить целиком
- **identity.md** — аналогично
- **Git history** — абсолютный запрет на удаление
- P0, P1, P2 имеют **неизменяемое семантическое ядро** (формулировку можно уточнять, но нельзя инвертировать)

### Тест "Корабля Тесея"

Защита от постепенного размывания принципов через "мелкие правки": если новая формулировка убрана, остаётся ли исходный принцип узнаваемым?

### Жёсткие ограничения

- **НЕЛЬЗЯ**: платежи, утечка секретов, нарушение закона, необратимое удаление
- **ВСЁ ОСТАЛЬНОЕ РАЗРЕШЕНО**: VLM, аккаунты, браузерная автоматизация, самомодификация
