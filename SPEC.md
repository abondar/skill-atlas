# skill-atlas — спецификация v1

Статус: v1 реализована (раздел 15). Дата: 2026-09-30.

## 1. Цель

`skill-atlas` находит скиллы AI-агентов в репозитории, показывает их в TUI и сохраняет снапшот в локальную базу.
Снапшот привязан к репо и коммиту. Тула агрегирует снапшоты и показывает, какие репо и скиллы известны.

Потребитель снапшотов — сама тула. Внешние потребители в v1 не поддерживаются, но схема JSON стабильна и версионирована.

## 2. Термины

| Термин | Значение |
| - | - |
| Скилл | Инструкция, которую агент загружает по запросу как процедуру. Определение — раздел 5. |
| Агент | Определение субагента (персоны). Хранится как `kind: agent`, в скиллы не входит. |
| Цель скана | GitHub-репо или локальный путь. |
| Снапшот | Один иммутабельный JSON-файл с результатом одного скана. |
| База | Каталог со снапшотами. |
| `repo_key` | Стабильный ключ репо для агрегации. Раздел 7.2. |
| Детектор | Правило «путь → тип записи». Раздел 5.2. |

## 3. Интерфейс CLI

### 3.1. `skill-atlas scan <target>`

```
skill-atlas scan <target>
  [--ref <branch|tag|sha>]     # по умолчанию default branch (GitHub) или HEAD (локально)
  [--path <subdir>]            # скан только подкаталога
  [--include <glob>]...        # отменяет исключение фикстур и --exclude для совпавших путей
  [--exclude <glob>]...        # дополнительные exclude-паттерны
  [--no-tui]                   # без TUI, итог в stdout
  [--output <file>|-]          # дополнительно записать снапшот в файл или stdout
  [--no-save]                  # не писать снапшот в базу
  [--force]                    # писать новый снапшот даже при cache hit
  [--host <host>]              # GitHub Enterprise; по умолчанию github.com
  [--plain]                    # текстовый отчёт в stdout вместо TUI
  [--with-body]                # с --plain: включить тела скиллов
  [--quiet, -q]                # не показывать прогресс в stderr
```

Формы `<target>`:

| Форма | Пример | Тип |
| - | - | - |
| Существующий путь | `.`, `./o/r`, `/abs/path` | local |
| HTTPS URL | `https://github.com/o/r`, `https://github.com/o/r.git` | github |
| URL с ref и путём | `https://github.com/o/r/tree/<ref>/<path>` | github |
| SSH URL | `git@github.com:o/r.git` | github |
| Короткая форма | `o/r` | github |

Правило разрешения неоднозначности: если `<target>` — существующий путь на диске, тула сканирует его как local и печатает предупреждение, когда строка также похожа на `o/r`.
Флаги `--ref` и `--path` имеют приоритет над ref и путём из URL.

Поведение:
- По умолчанию `scan` сразу открывает TUI со списком найденных скиллов.
- Во время скана тула показывает прогресс в stderr: этап, время этапа и детали (прогресс `git fetch`, счётчик файлов). На терминале это спиннер, без терминала — по строке на этап. `--quiet` отключает прогресс.
- `--no-tui` не открывает TUI: тула сохраняет снапшот и печатает сводную таблицу.
- `--plain` печатает текстовый отчёт: одна строка `ключ: значение` на поле, блок `## <name>` на скилл, без цветов и таблиц. Формат предназначен для скриптов и AI-агентов. `--plain` несовместим с `--output -`.
- Без TTY (pipe, CI) тула не запускает TUI и работает как `--no-tui`.
- По умолчанию тула сохраняет снапшот в базу.
- `--no-tui` печатает сводку: `repo@sha`, число скиллов и агентов, путь к снапшоту.

### 3.2. Команды агрегации

```
skill-atlas                                   # TUI по всей базе
skill-atlas --web [--web-host H] [--web-port N] [--no-browser]   # веб-UI по всей базе
skill-atlas repos  [--json] [--sort scanned|skills|name]
skill-atlas skills [--name <substr>] [--repo <repo_key>] [--type <type>]
                   [--category relevant|auxiliary|all|<category>]
                   [--group-by none|name|hash] [--all-scans] [--json]
skill-atlas show   <repo_key>[@sha] | <snapshot.json> [--plain] [--with-body]
```

- `repos` выводит: `repo_key`, дату последнего скана, `commit_sha` последнего скана, число скиллов, число сканов.
- `skills` берёт последний снапшот каждого репо. `--all-scans` берёт все снапшоты.
- `skills --category` по умолчанию `relevant`: тестовые данные, примеры, шаблоны и документация не искажают счётчики (раздел 5.6).
- `--group-by name` группирует по имени: число репо и число разных `content_sha256`.
- `--group-by hash` группирует по содержимому: показывает копии одного скилла в разных репо.
- `show` открывает TUI на сохранённом снапшоте без сети.
- `skill-atlas` без команды открывает TUI по всей базе (раздел 8). Без TTY тула печатает help.

### 3.3. Exit codes

| Код | Значение |
| - | - |
| 0 | Успех, включая 0 найденных скиллов |
| 1 | Внутренняя ошибка |
| 2 | Неверные аргументы или неразбираемый `<target>` |
| 3 | Нет доступа: 401, 403, 404 на приватном репо, ref не найден |
| 4 | Сеть или rate limit |
| 5 | Ошибка записи или чтения базы |

Частичные ошибки (файл не прочитался, битый YAML) не меняют exit code. Тула пишет их в `warnings`.

## 4. Получение данных

### 4.1. GitHub

Порядок поиска токена: `GITHUB_TOKEN`, `GH_TOKEN`, вывод `gh auth token`. Без токена тула работает с публичными репо в пределах анонимного лимита.

Шаги:
1. `GET /repos/{o}/{r}` — метаданные, `default_branch`, `node_id`.
2. `GET /repos/{o}/{r}/commits/{ref}` — резолв ref в полный SHA и `commit_date`.
3. `GET /repos/{o}/{r}/git/trees/{sha}?recursive=1` — полное дерево файлов.
4. Детекторы работают по дереву. Тула скачивает содержимое только файлов-кандидатов (раздел 5.2) через `raw.githubusercontent.com/{o}/{r}/{sha}/{path}`, до 8 файлов параллельно. Для GitHub Enterprise тула читает файлы через contents API. Symlink тула читает через blobs API.
5. Ресурсы скилла тула не скачивает. В снапшот попадают путь, размер и `git_blob_sha` из дерева.

Fallback: если ответ дерева содержит `truncated: true`, тула делает `git fetch --depth 1 --filter=blob:none` одного коммита во временный каталог. Clone нужен только для листинга: `git ls-tree` без `-l` и с `GIT_NO_LAZY_FETCH=1`. С `-l` git запрашивает каждый blob отдельным сетевым запросом, и на больших репо скан зависает. После листинга тула удаляет clone. Содержимое кандидатов тула читает по HTTP, как в API-режиме. В этом режиме `resources[].bytes` = `null`.
Зависшую передачу git прерывает сам: `http.lowSpeedLimit=1000`, `http.lowSpeedTime=60`.
В снапшоте тула пишет `scan.fetch_method: api | clone`.

Rate limit: при 403/429 с `X-RateLimit-Remaining: 0` тула завершается с кодом 4. Сообщение содержит время сброса. Автоматических ретраев на rate limit нет.
Сетевые ошибки 5xx тула повторяет до 3 раз с экспоненциальной задержкой.

Приватный репо без токена отвечает 404. Тула сообщает: «репо не найден или нет доступа; задайте GITHUB_TOKEN».

### 4.2. Локальный путь

- Если путь внутри git-репо, тула берёт `HEAD` SHA, `commit_date`, `remote.origin.url` и флаг `dirty` (`git status --porcelain` не пуст).
- `--ref` для локального пути читает дерево этого ref через `git`, а не рабочую копию. Тогда `dirty` не применяется.
- Без git: `commit_sha: null`, `dirty: null`.
- Тула не требует, чтобы `remote.origin.url` указывал на GitHub.

## 5. Детекция

### 5.1. Что скилл, а что нет

| Категория | Файлы | Запись |
| - | - | - |
| Скилл по стандарту Agent Skills | каталог с файлом ровно `SKILL.md`, в любом месте репо | `kind: skill` |
| Скилл устаревшего формата | `.claude/commands/**/*.md`, `commands/` плагина, `.github/prompts/*.prompt.md` | `kind: skill` |
| Агент | `.claude/agents/**/*.md`, `agents/` плагина, `.github/agents/*.agent.md` | `kind: agent` |
| Не скилл | `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `*.instructions.md`, `.cursor/rules/**`, `.cursorrules`, `.windsurfrules`, `README.md`, прочие `.md` | не записывается |
| Ресурс скилла | любой файл внутри каталога скилла, кроме вложенных скиллов | `resources[]` родителя |

Почему такая граница: правила и инструкции агент грузит всегда или по glob, а скилл — по запросу пользователя или по решению модели.

### 5.2. Детекторы

| ID | Паттерн | kind | type |
| - | - | - | - |
| D1 | `**/SKILL.md` | skill | `agent-skill` |
| D2 | `**/.claude/commands/**/*.md` | skill | `claude-command` |
| D3 | `<plugin_root>/commands/**/*.md` или пути из `plugin.json#commands` | skill | `plugin-command` |
| D4 | `plugin.json#commands.<name>.content` (inline, без файла) | skill | `plugin-command-inline` |
| D5 | `**/.github/prompts/*.prompt.md` | skill | `copilot-prompt` |
| D6 | `**/.claude/agents/**/*.md`, `<plugin_root>/agents/**/*.md` или пути из `plugin.json#agents` | agent | `claude-agent` |
| D7 | `**/.github/agents/*.agent.md` | agent | `copilot-agent` |
| D8 | запись `marketplace.json` с удалённым `source` | skill | `external-plugin` |

Плагины:
- `plugin_root` — каталог, который содержит `.claude-plugin/plugin.json`. Каталог с `.claude-plugin/marketplace.json` — корень маркетплейса.
- Тула парсит `plugin.json` и учитывает семантику путей: `skills` добавляется к `skills/`, а `commands` и `agents` заменяют каталоги по умолчанию.
- Тула парсит `marketplace.json`. Плагины с локальным `source` тула сканирует как `plugin_root`. Плагины с удалённым `source` тула записывает в `plugins[]` с `id = <marketplace_path>#<name>` и не скачивает. Для каждого такого плагина тула добавляет запись D8: заглушку с `name` и `description` из маркетплейса, `extras.remote_source` и warning с адресом источника. Тела и `content_sha256` у заглушки нет.
- Корень с `plugin.json` — один плагин (`id` = путь корня или `.`); записи маркетплейса с тем же корнем дополняют его. Без `plugin.json` каждая запись маркетплейса — отдельный плагин. Если на один корень ссылается несколько записей, `id = <root>#<name>`.
- Запись, у которой `source` — корень маркетплейса, и которая задаёт `skills`, загружает только перечисленные каталоги; каталог `skills/` по умолчанию не сканируется. Так устроен `anthropics/skills`.
- Каждый скилл внутри плагина получает `plugin_id`.

`scope` для D1 — ближайший известный корень над каталогом скилла:
`.agents/skills`, `.claude/skills`, `.cursor/skills`, `.codex/skills`, `.github/skills`, `.opencode/skills`, `<plugin_root>/skills`. Если корня нет — `scope: unscoped`. Для плагинного скилла `scope: plugin`.
Для остальных детекторов `scope` = `plugin` (компонент плагина), `.claude` (D2, D6) или `.github` (D5, D7).

### 5.3. Исключения по умолчанию

Каталоги: `.git`, `node_modules`, `vendor`, `.venv`, `venv`, `dist`, `build`, `target`, `__pycache__`.
Тестовые данные тула не исключает: они попадают в снапшот с категорией `test` (раздел 5.6). Так их можно найти без повторного скана.
`scan.excluded_candidates` считает кандидатов, исключённых `--exclude`. Кандидаты в каталогах по умолчанию не считаются: тула не обходит эти каталоги. `--include` отменяет только `--exclude`; каталоги по умолчанию исключены всегда.

### 5.4. Правила разбора

- Frontmatter тула читает только если файл начинается с `---` в первой строке. Так же работает Claude Code.
- YAML тула парсит safe-загрузчиком без тегов и алиасов. Битый YAML даёт `frontmatter_error`, скилл остаётся в снапшоте.
- `name`: из frontmatter; иначе имя каталога (D1) или stem файла (D2, D3, D5–D7); для D4 и map-записей D3 — ключ в `commands`. Поле `name_source` фиксирует источник.
- D2 и D3 игнорируют `name` во frontmatter: Claude Code берёт имя команды из пути. Вложенные каталоги дают имя через `:` (`git/commit.md` → `git:commit`).
- `description`: из frontmatter; иначе первая непустая строка тела. Поле `description_source` фиксирует источник.
- Вложенный `SKILL.md` внутри каталога другого скилла — отдельный скилл. Его каталог тула не включает в `resources[]` родителя.
- `agents/openai.yaml` внутри каталога скилла тула скачивает и парсит в `extras.openai_yaml`.
- Symlink: тула разрешает цель внутри репо. Цель вне репо тула не читает и пишет warning. Дубли с одинаковым разрешённым путём тула сливает в одну запись с `aliases[]`.
- Git LFS pointer, бинарный файл, невалидный UTF-8, файл больше 1 MiB — `compliance.status: broken`, тело не сохраняется.
- Submodule тула помечает в `scan.warnings` и не обходит.

### 5.5. Compliance

Применяется к `type: agent-skill`. Для остальных типов `status` принимает значения `loadable` или `broken`.

| Статус | Условие |
| - | - |
| `compliant` | Все проверки спеки Agent Skills проходят |
| `loadable` | Файл читается, но есть хотя бы одно нарушение спеки |
| `broken` | Файл не читается как текст |

Проверки спеки (каждое нарушение идёт в `compliance.violations[]` с кодом):
- `name` есть, 1–64 символа, `[a-z0-9-]`, без `-` в начале и конце, без `--`;
- `name` совпадает с именем каталога;
- `description` есть, 1–1024 символа;
- `compatibility`, если есть, 1–500 символов;
- `metadata`, если есть, — map string→string.

Поля-расширения (`when_to_use`, `disable-model-invocation`, `user-invocable`, `context`, `model`, `paths`, `globs`, `icon`, `color`, `argument-hint`) не нарушают спеку. Тула хранит их в `frontmatter` без нормализации.

### 5.6. Категории

`type` описывает формат и загрузчик записи. `category` описывает, зачем запись лежит в репо. Так пользователь сразу отделяет скиллы, которые загружает агент, от тестовых данных и примеров. `category_reason` называет сработавшее правило, например `path segment "jvmTest"`.

Тула проверяет правила по порядку, первое совпадение задаёт категорию:

| Категория | Группа | Правило |
| - | - | - |
| `external` | relevant | запись D8 |
| `test` | auxiliary | сегмент `test`, `tests`, `__tests__`, `testdata`, `test_data`, `fixtures`, `__fixtures__`; префикс `test-`/`test_`; суффикс `-test(s)`/`_test(s)`; Gradle source set `<lower>Test` (`jvmTest`, `commonTest`) |
| `example` | auxiliary | сегмент `example(s)`, `sample(s)`, `demo(s)`, `showcase`, в том числе с суффиксом через `-`/`_` (`example-plugin`) |
| `template` | auxiliary | сегмент `template(s)`, `skeleton(s)`, `boilerplate(s)`, `scaffold(s)` |
| `docs` | auxiliary | сегмент `doc`, `docs`, `documentation` |
| `plugin` | relevant | компонент плагина |
| `project` | relevant | корень загрузки в корне репо |
| `subproject` | relevant | корень загрузки в подкаталоге |
| `template` | auxiliary | каталог скилла вне корней загрузки называется как шаблон (`template/SKILL.md` в `anthropics/skills`) |
| `bundled` | relevant | сегмент `resources` или `assets`: скилл поставляется внутри продукта |
| `catalog` | relevant | остальное, например коллекция `skills/` для установки |

Корни загрузки: пары из `scope` (раздел 5.2), а также `.claude/commands`, `.claude/agents`, `.github/prompts`, `.github/agents`.

Какие сегменты проверяют правила групп auxiliary и `bundled`:
- внутри корня загрузки — только сегменты до корня: `.claude/skills/tests/unit/` — `project`, `tests/fixtures/x/.claude/skills/` — `test`;
- для компонента плагина — сегменты `plugin_root`;
- иначе — сегменты над каталогом скилла; имя самого каталога не проверяется, поэтому скилл `test-runner` не становится тестом.

Слова `testing`, `spec`, `latest`, `contest` не считаются маркерами теста.

TUI и `skills` по умолчанию показывают группу `relevant`. Записи из снапшотов detectors v1 не имеют категории и считаются `relevant`: v1 не сохранял фикстуры.

### 5.7. Похожие скиллы

Вкладка Similar помогает найти частичные дубликаты внутри одного снапшота и решить, какие скиллы объединить. Тула считает похожесть при просмотре, в снапшот результат не пишет. Алгоритм детерминирован, не использует модель и сеть (`skill_atlas/similarity.py`).

Кандидаты: все записи снапшота, кроме `external`. Идентичные копии (`kind`, `name`, `content_sha256`) — одна запись; они видны в Overview, а не в Similar. Записи с тем же `kind` и `name`, но другим содержимым — это версии того же скилла, которые разошлись (например, `.agents/skills/x` и `.claude/skills/x`). Скилл не бывает похожим на самого себя: Similar их не показывает. Overview перечисляет их в Locations с пометкой «different content» и долей общего текста; вкладка Similar упоминает их число.

Два сигнала:
- **Vocabulary** (`topic`): косинус TF-IDF векторов. Слова берутся из `name` (вес 3), `description` (вес 2) и тела (вес 1). Токенизация: слова `[A-Za-z][A-Za-z0-9]*`, идентификаторы разбиваются по регистру (`GradleBuild` → `gradle`, `build`), стоп-слова удаляются, лёгкий стеммер снимает окончания `-s`, `-es`, `-ies`, `-ing`, `-ed`. TF сублинейный (`1 + ln tf`), IDF сглаженный и считается по записям этого снапшота: слова, которые есть в каждом скилле, почти ничего не весят. Вектор обрезается до 200 самых тяжёлых термов.
- **Shared text** (`overlap`): доля 5-словных шинглов тела одной записи, которые есть в теле другой. Тула показывает обе доли: «X% этого скилла есть в том» и «Y% того есть в этом». Так видно, какой скилл содержит другой. Если у одной из записей меньше 20 шинглов, сигнал равен 0: короткий общий фрагмент ничего не значит.

`score = max(topic, max(overlap в обе стороны))`. Score симметричен и не зависит от порядка записей. Порог — 40%: Similar показывает только записи с `score ≥ 0.4`, от большего к меньшему. Уровни: `near-identical` от 90%, `strong` от 60%, `related` ниже. Для каждой записи тула показывает до 8 общих термов с наибольшим вкладом в косинус. Проценты округляются вниз: 99.6% выводится как 99%, а 100% значит полное совпадение.

Ограничение: сходство лексическое. Два скилла, которые описывают одну задачу разными словами, получают низкий score. Эмбеддинги поймали бы перефразирование, но требуют модели, зависят от её версии и медленнее.

Калибровка на реальных репо (порог 40%): в `anthropics/skills` и `openai/codex` пар нет (максимум 31% и 24%); в `JetBrains/MPS` — ни одной (максимум 38%); в `anthropics/claude-plugins-official` — 46 пар, в том числе `access` в плагинах telegram/discord/imessage (84–93%) и `code-simplifier` в двух плагинах (97%); в `JetBrains/ultimate` — 188 пар, 115 из них near-identical: копии в `community/` и в корне, которые разошлись в деталях. Индекс для 300 скиллов строится за 0.4 с, запрос по одному скиллу — 4 мс.

## 6. Модель данных снапшота

Формат: JSON, UTF-8, ключи отсортированы, отступ 2. Массивы тула сортирует детерминированно: `skills` по `id`, `resources` по `path`.
Два скана одного SHA с одинаковыми опциями дают одинаковый файл, кроме `scan.id`, `scan.scanned_at` и `scan.duration_ms`.

```json
{
  "schema_version": 1,
  "scan": {
    "id": "01J8Z6K3M2...",
    "scanned_at": "2026-09-30T14:12:03.512Z",
    "tool_version": "0.1.0",
    "detectors_version": 2,
    "fetch_method": "api",
    "duration_ms": 2140,
    "options": { "ref": null, "path": null, "include": [], "exclude": [] },
    "excluded_candidates": 3,
    "warnings": []
  },
  "source": {
    "kind": "github",
    "repo_key": "github.com/anthropics/skills",
    "host": "github.com",
    "owner": "anthropics",
    "name": "skills",
    "url": "https://github.com/anthropics/skills",
    "node_id": "R_kgDO...",
    "requested_ref": null,
    "resolved_ref": "main",
    "commit_sha": "3f2a9c1b...40 hex",
    "commit_date": "2026-09-28T09:00:00Z",
    "local_path": null,
    "remote_url": null,
    "dirty": null
  },
  "repo": {
    "description": "...",
    "default_branch": "main",
    "license": "Apache-2.0",
    "topics": ["agent-skills"],
    "stars": 12345,
    "forks": 678,
    "visibility": "public",
    "archived": false,
    "is_fork": false,
    "pushed_at": "2026-09-28T09:00:00Z"
  },
  "plugins": [
    {
      "id": "plugins/deploy-tools",
      "root": "plugins/deploy-tools",
      "manifest_path": "plugins/deploy-tools/.claude-plugin/plugin.json",
      "name": "deploy-tools",
      "version": "1.2.0",
      "description": "...",
      "marketplace_path": ".claude-plugin/marketplace.json",
      "remote_source": null
    }
  ],
  "skills": [
    {
      "id": "agent-skill:skills/pdf/SKILL.md",
      "kind": "skill",
      "type": "agent-skill",
      "detector": "D1",
      "scope": "unscoped",
      "path": "skills/pdf/SKILL.md",
      "dir": "skills/pdf",
      "source_pointer": null,
      "plugin_id": null,
      "category": "catalog",
      "category_reason": "outside agent load roots and plugins",
      "name": "pdf",
      "name_source": "frontmatter",
      "description": "Extract text and tables from PDF files...",
      "description_source": "frontmatter",
      "frontmatter": { "name": "pdf", "description": "...", "license": "Proprietary" },
      "frontmatter_error": null,
      "body": "# PDF\n...",
      "body_bytes": 8123,
      "body_lines": 210,
      "content_sha256": "…",
      "git_blob_sha": "…",
      "resources": [
        { "path": "skills/pdf/scripts/extract.py", "bytes": 2048, "git_blob_sha": "…" }
      ],
      "extras": { "openai_yaml": null },
      "aliases": [],
      "compliance": { "status": "compliant", "violations": [] },
      "warnings": []
    }
  ],
  "stats": {
    "skills": 1,
    "agents": 0,
    "external": 0,
    "by_type": { "agent-skill": 1 },
    "by_compliance": { "compliant": 1 },
    "by_category": { "catalog": 1 }
  }
}
```

Правила полей:
- `repo` — `null` для локального скана без GitHub remote. Для локального скана с GitHub remote тула не ходит в API; `repo` = `null`.
- `id` = `<type>:<path>`. Для D4 — `plugin-command-inline:<manifest_path>#<name>`, `path: null`, `source_pointer: "<manifest_path>#/commands/<name>"`.
- `fetch_method`: `api` (GitHub Trees API), `clone` (fallback), `fs` (рабочая копия на диске), `git` (локальная ревизия через `--ref`).
- `frontmatter_raw` хранит текст frontmatter как есть; `frontmatter` — разобранный mapping или `null`.
- `content_sha256` считается от исходных байтов файла (или inline `content` для D4), до санитизации.
- `body` хранит оригинал. Санитизация — только при рендере (раздел 9).
- Содержимое ресурсов в снапшот не входит.
- `stats.skills` и `stats.agents` не считают заглушки D8; их считает `stats.external`.
- Snapshots v1 с `scan.options.include_fixtures` тула читает: загрузчик удаляет это поле. Смена `detectors_version` на 2 отменяет cache hit по старым снапшотам.

### 6.1. Версионирование схемы

- Любое несовместимое изменение увеличивает `schema_version`.
- Агрегатор читает все известные версии через функции миграции в памяти. Файлы на диске тула не переписывает.
- Снапшот с неизвестной (более новой) версией агрегатор пропускает с предупреждением.
- Изменение детекторов увеличивает `detectors_version`. Это влияет на cache hit.

## 7. База

### 7.1. Расположение и имена файлов

Каталог: `$SKILL_ATLAS_HOME/scans/`. По умолчанию `$SKILL_ATLAS_HOME` = `$XDG_DATA_HOME/skill-atlas`, иначе `~/.local/share/skill-atlas`.
Каталог плоский, без подкаталогов.

Имя файла:

```
<scanned_at>_<repo_slug>_<sha8|nosha>[_dirty].json
20260930T141203.512Z_github.com-anthropics-skills_3f2a9c1b.json
20260930T142010.004Z_local-my-project-7c1e04aa_nosha.json
```

- `scanned_at` в UTC, формат `YYYYMMDDTHHMMSS.mmmZ`. Лексикографический порядок совпадает с хронологическим.
- `repo_slug` = `repo_key` в нижнем регистре, символы вне `[a-z0-9.-]` заменены на `-`.
- Имя файла служит только для сортировки и предварительного фильтра. Источник правды — содержимое JSON. Тула никогда не восстанавливает `repo_key` из имени.
- Запись атомарная: временный файл в том же каталоге, затем `rename`. Если имя занято, тула добавляет суффикс `-1`, `-2`.
- Тула никогда не изменяет и не удаляет существующие снапшоты.

### 7.2. `repo_key`

| Источник | `repo_key` |
| - | - |
| GitHub | `<host>/<owner>/<name>` в нижнем регистре |
| Локальный git с remote на известный GitHub host | тот же ключ, что у GitHub |
| Локальный git с другим remote | нормализованный remote: `<host>/<path>` без `.git` |
| Локальный без remote | `local/<basename>-<sha8(абсолютный путь)>` |

Переименование или перенос репо на GitHub меняет `owner/name`. Агрегатор связывает такие снапшоты по `source.node_id`, если он есть у обоих.

### 7.3. Cache hit

Cache hit — в базе уже есть снапшот с тем же `repo_key`, `commit_sha`, `detectors_version`, `options`, и `dirty` не `true`.
При cache hit тула не пишет новый файл и открывает найденный снапшот. `--force` пишет новый.
Резолв ref в SHA тула выполняет всегда, так что сетевой запрос при cache hit остаётся.

### 7.4. Агрегация

- «Последний снапшот репо» — максимальный `scan.scanned_at` из содержимого среди снапшотов с одним `repo_key` (или `node_id`).
- Тула читает все файлы каталога. Индекс в v1 отсутствует.
- Будущий индекс (после v1) — производный кэш. Тула пересобирает его из каталога, он никогда не источник правды.

## 8. TUI

Фреймворк: Textual.

Экран репозиториев (`skill-atlas` без команды):
- Левая панель: репозитории базы, последний скан сверху. Колонки: `repo`, `skills`, `agents`, `scans`, `last scan`, `commit`.
- Правая панель: описание репо, счётчики, параметры последнего скана (как вкладка Scan) и история сканов.
- `Enter` открывает последний снапшот репо на экране снапшота. `Esc` на экране снапшота возвращает к списку.
- `n` открывает поле ввода цели: GitHub URL, `owner/repo` или локальный путь. `Enter` запускает скан с опциями по умолчанию в фоновом потоке. TUI остаётся отзывчивым, строка статуса показывает этапы скана. После скана тула перечитывает базу и открывает снапшот. Ошибка скана приходит уведомлением; список остаётся на месте. Одновременно идёт только один скан.

Экран снапшота (`scan`, `show`):
- Шапка: `repo_key@sha8`, дата коммита, `dirty`, число скиллов и агентов.
- Левая панель: список записей. Колонки: `name`, `copies`, `category`, `type`, `compliance`, `path`. Записи группы auxiliary выводятся приглушённо.
- Правая панель, вкладки: Overview (ключевые поля и метаданные репо), Frontmatter (YAML как есть), Body (отрендеренный markdown), Resources (список файлов), Warnings, Scan (когда и как тула сделала снапшот: время, длительность, `fetch_method`, версия тулы, ref, коммит, опции скана, файл снапшота), Similar (похожие скиллы, раздел 5.7; индекс строится при первом открытии вкладки).
- Строка статуса: активные фильтры, число записей, скрытых фильтром категорий, и путь к снапшоту.

Группировка копий. Тула уже сворачивает symlink-копии в `aliases` (раздел 5). Реальные копии одного скилла (например, в `.claude/skills` и `.agents/skills`) остаются отдельными записями снапшота. TUI группирует записи с одинаковыми `kind`, `name` и `content_sha256` в одну строку. Колонка `copies` показывает `+N`, Overview перечисляет пути копий. Группировка работает только при отображении: снапшот, `--plain` и агрегация видят все записи. Сравнение идёт только по основному файлу: копии с разными ресурсами попадают в одну строку.

Клавиши:

| Клавиша | Действие |
| - | - |
| `↑/↓`, `j/k` | навигация |
| `/` | поиск по `name`, `description`, `path` |
| `g` | переключить группу категорий: relevant → auxiliary → все (по умолчанию relevant) |
| `f` | переключить фильтр `kind`: skill → agent → все (по умолчанию skill) |
| `t` | переключить фильтр `type` |
| `c` | переключить фильтр `compliance` |
| `d` | группировать копии / показать каждую запись (по умолчанию группировать) |
| `Enter` | экран репозиториев: открыть последний снапшот |
| `n` | экран репозиториев: просканировать новый репо |
| `Esc` | закрыть поиск; на экране снапшота из списка репо — назад |
| `tab` | переключение панели |
| `1`–`7` | вкладки |
| `o` | открыть файл на GitHub по permalink `blob/<sha>/<path>` (только github) |
| `e` | экспорт снапшота в выбранный путь |
| `q` | выход |

### 8.1. Веб-UI

`skill-atlas --web` запускает локальный HTTP-сервер с веб-приложением. Функции те же, что у TUI, но интерфейс — обычное веб-приложение, а не копия терминала:

- «Repositories»: сводные счётчики (репо, скиллы, агенты, внешние плагины) и сетка карточек репо: имя с исходным регистром, описание, полоса распределения по категориям с легендой, звёзды, время последнего скана. Фильтр по имени и сортировка.
- Страница репо: заголовок с коммитом, ref, лицензией, звёздами и ссылкой на GitHub; выбор снапшота из истории; кнопки Rescan и Export JSON. Вкладки: Skills (карточки скиллов), Scan details (как и когда сделан снапшот), History (таймлайн сканов с изменением числа скиллов).
- Фильтры скиллов: сегмент Relevant / Tests & examples / All со счётчиками, чипы категорий, kind, type, compliance, поиск, переключатель группировки копий (`N locations` на карточке).
- Боковая панель скилла: описание, нарушения спеки, предупреждения, все местоположения (основное, идентичные копии, symlink) с копированием пути и permalink; детали; вкладка Content (frontmatter и отрендеренный markdown); вкладка Files (ресурсы с размерами); вкладка Similar (раздел 5.7): число похожих в заголовке вкладки, карточки со score и уровнем, полоса Vocabulary, доли Shared text в обе стороны, общие термы; клик открывает похожий скилл.
- «Skills»: поиск по скиллам из последних снапшотов всех репо, группировка по имени, число репо и вариантов содержимого; чип репо открывает скилл на странице репо. В TUI такой страницы нет.
- Скан: модальное окно с примерами, пошаговым прогрессом (завершённые этапы со временем, текущий этап с деталями), фоновым режимом (индикатор в верхней панели), ошибкой внутри окна. После скана UI открывает страницу репо.
- Светлая и тёмная тема по `prefers-color-scheme`, адаптивная вёрстка до 390 px. Клавиши: `/` — поиск, `Esc` — закрыть панель или окно.

- Сервер: stdlib `ThreadingHTTPServer`. Фронтенд — vanilla JS и CSS без CDN и сборки; UI работает офлайн.
- Адрес по умолчанию `127.0.0.1`, порт выбирает ОС (`--web-port 0`). Тула печатает URL в stderr и открывает браузер; `--no-browser` отключает браузер. `Ctrl+C` останавливает сервер.
- Состояние вида (снапшот, фильтры, вкладка, выбранная запись) хранится в URL-фрагменте: ссылку можно сохранить, кнопка «Назад» работает.
- API: `GET /api/repos`, `GET /api/skills?category=` (скиллы последних снапшотов без тел), `GET /api/snapshot?file=`, `GET /api/snapshot/raw?file=` (исходный JSON как attachment), `GET /api/similar?file=&id=` (похожие скиллы; сервер держит индексы последних 4 снапшотов в памяти), `POST /api/scan` → задача, `GET /api/scan/<id>` → состояние задачи с завершёнными этапами. Одновременно идёт один скан (`409` на второй).
- `--web` вместе с командой — ошибка использования (exit 2). Занятый порт — ошибка с exit 1.

Безопасность веб-UI:
- Токен: тула генерирует случайный токен на каждый запуск. URL `/?token=…` ставит cookie `HttpOnly; SameSite=Strict` и перенаправляет на `/` без токена. Без cookie API отвечает `401`.
- Заголовок `Host` должен быть `localhost`, `127.0.0.1`, `[::1]` или адресом привязки с портом сервера. Иначе `403`: так страница через DNS rebinding не прочитает базу.
- `POST /api/scan` требует заголовок `X-Skill-Atlas: 1` и `Content-Type: application/json`. Форма с чужого сайта не может выполнить эти условия без preflight.
- Имя снапшота — только имя файла из базы: без `/`, `\`, ведущей точки, с расширением `.json`. Остальное — `404`.
- Сервер санитизирует все строки снапшота (раздел 9). Фронтенд вставляет их только как текст. Исключение — `body_html`: сервер рендерит Markdown через `markdown-it-py` с выключенным raw HTML; `javascript:` ссылки не становятся ссылками; изображения заменяются текстом `[image: alt] url`, чтобы скилл не мог отследить читателя внешним пикселем.
- Заголовки: `Content-Security-Policy: default-src 'none'; script-src 'self'; …` (inline-скрипты запрещены), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
- `--web-host` с не-loopback адресом тула разрешает, но печатает предупреждение: любой, у кого есть URL, читает базу и запускает сканы. Трафик идёт без TLS.

## 9. Безопасность

- Тула только читает. Она не исполняет файлы из репо, не запускает хуки и не ставит зависимости.
- Санитизация при любом выводе в терминал (TUI и `--no-tui`):
  - удалить C0 (кроме `\n`, `\t`) и C1 control chars;
  - удалить ANSI CSI, OSC (включая OSC 8), DCS, APC, PM, SOS последовательности;
  - заменить bidi-override и isolate символы (U+202A–U+202E, U+2066–U+2069) на видимый маркер `⟦bidi⟧`;
  - в рендере markdown отключить терминальные гиперссылки; URL показывать текстом.
- Лимиты: файл-кандидат до 1 MiB; до 5000 кандидатов на скан; до 200 000 записей дерева. Превышение даёт warning и остановку обработки лишних записей.
- YAML: safe-загрузка, лимит глубины вложенности 32, лимит размера frontmatter 64 KiB.
- Токен не попадает в снапшот, логи и сообщения об ошибках.
- `--output` и экспорт не перезаписывают существующий файл без `--force`.

## 10. Нефункциональные требования

- Python 3.12+, macOS и Linux. Windows вне v1.
- `repos`, `skills`, `show` работают без сети.
- Скан публичного репо на 10 000 файлов через API — до 10 с при 20 кандидатах.
- Агрегация 1000 снапшотов — до 2 с.
- Покрытие тестами детекции и санитизации — все правила из разделов 5 и 9 имеют тест.

Стек: `uv`, `typer`, `textual`, `httpx`, `PyYAML` (SafeLoader с проверкой событий на теги, якоря и глубину), `pydantic` для схемы, `pytest` + `respx` + `hypothesis`, `ruff`, `mypy --strict`.

## 11. Вне скоупа v1

GitLab и Bitbucket; скан организации или списка репо; оценка качества скиллов; установка скиллов; команда `diff`; индекс базы; Windows; обход удалённых плагинов из `marketplace.json`; Cursor-плагины `.cursor-plugin/`; команды Gemini CLI (`.toml`).

## 12. План реализации

Каждый этап заканчивается зелёными `ruff`, `mypy`, `pytest` в CI.

### M0. Каркас
- Проект на `uv`, entrypoint `skill-atlas`, `typer` с заглушками команд.
- CI: lint, typecheck, tests.
- Готово, когда: `skill-atlas --help` показывает все команды; CI зелёный.

### M1. Модель и детекция на локальном каталоге
- Pydantic-модель снапшота, JSON Schema генерируется из модели и лежит в репо.
- Абстракция источника: `list_tree()`, `read_file(path)`. Реализация для файловой системы.
- Детекторы D1–D7, парсинг `plugin.json` и `marketplace.json`, исключения, symlink-дедупликация, compliance.
- Набор фикстур-репо в `tests/fixtures/repos/`:
  - по одному на каждый детектор;
  - inline-команда в `plugin.json`;
  - `plugin.json` с переопределёнными путями;
  - symlink `.claude/skills → .agents/skills`;
  - битый YAML, файл без frontmatter, `name` не совпадает с каталогом;
  - вложенный `SKILL.md`;
  - шум: `node_modules`, `tests/fixtures`, `AGENTS.md`, `.cursor/rules`;
  - LFS pointer, файл > 1 MiB, бинарный `SKILL.md`.
- Golden-тесты: снапшот каждой фикстуры совпадает с эталонным JSON.
- Готово, когда: `scan <dir> --no-tui --no-save --output -` выдаёт детерминированный JSON для всех фикстур.

### M2. База и локальный git
- Идентичность локального git: `HEAD`, `dirty`, remote, `repo_key`.
- `--ref` для локального git через `git ls-tree` / `git show`.
- Запись в базу: имена файлов, атомарность, коллизии, cache hit, `--force`.
- Готово, когда: два скана одного чистого коммита создают один файл; скан грязного дерева всегда создаёт новый.

### M3. GitHub
- Разбор всех форм `<target>` и правило неоднозначности.
- Поиск токена, API-клиент, Trees API, raw-загрузка кандидатов, fallback на `git clone --filter=blob:none`.
- Метаданные репо, `node_id`, обработка 401/403/404/429, ретраи 5xx, exit codes.
- Тесты на записанных HTTP-ответах (`respx`). Отдельный ручной smoke-тест на 3–5 публичных репо со скиллами и одном репо без скиллов. Список репо фиксируется в начале этапа.
- Готово, когда: локальный и удалённый скан одного коммита дают одинаковые `skills[]`.

### M4. Агрегация
- Чтение базы, миграции схемы в памяти, пропуск неизвестных версий.
- `repos`, `skills` с `--group-by`, `--all-scans`, `--json`.
- Связывание по `node_id`.
- Тест производительности: 1000 синтетических снапшотов.
- Готово, когда: `repos` и `skills` работают без сети и укладываются в лимит из раздела 10.

### M5. TUI
- Экраны из раздела 8, поиск, фильтры, экспорт, `o` для permalink.
- Слой санитизации как единственный путь вывода текста из снапшота в терминал.
- Fuzz-тест санитизации (`hypothesis`) на случайных escape-последовательностях.
- `show` для `repo_key[@sha]` и для пути к файлу.
- Готово, когда: снапшот с ANSI, OSC 8 и bidi в `description` и `body` рендерится без побочных эффектов в терминале.

### M6. Доводка
- Лимиты из раздела 9, сообщения об ошибках, `README` с примерами.
- Готово, когда: все требования разделов 3–10 закрыты тестом или явно отмечены как ручная проверка.

## 13. Открытые вопросы

1. Cache hit не создаёт новый файл (раздел 7.3). Если нужна история «каждый запуск = файл», поведение по умолчанию нужно инвертировать.
2. Для вложенных `.claude/commands/a/b.md` тула выбрала имя `a:b`. С поведением Claude Code правило не сверено.
3. `body` в снапшоте увеличивает размер базы. При больших объёмах тела можно вынести в content-addressed хранилище по `content_sha256`.
4. Нужны ли агенты (`kind: agent`) в TUI по умолчанию или только через фильтр.
5. Для локального скана с GitHub remote тула не запрашивает метаданные репо. Нужно решить, допустим ли сетевой запрос в этом режиме.

## 14. Источники

- [Agent Skills Specification](https://agentskills.io/specification)
- [Adding skills support — .agents/skills convention](https://agentskills.io/client-implementation/adding-skills-support)
- [Claude Code — Skills](https://code.claude.com/docs/en/skills)
- [Claude Code — Plugin manifest reference](https://code.claude.com/docs/en/plugins-reference)
- [Cursor — Skills](https://cursor.com/docs/context/skills)
- [Cursor — Rules](https://cursor.com/docs/context/rules)
- [Codex — Build skills](https://learn.chatgpt.com/docs/build-skills)
- [VS Code Copilot — Customization overview](https://code.visualstudio.com/docs/copilot/customization/overview)

## 15. Статус реализации

Этапы M0–M6 реализованы. Проверки: `ruff check`, `ruff format --check`, `mypy --strict`, 174 теста `pytest` на Python 3.12 и 3.14.

Покрытие тестами:
- детекторы D1–D7, плагины, маркетплейсы, symlink, compliance, исключения, битые файлы — golden-снапшоты по 13 фикстурам;
- одинаковые `skills[]` при скане рабочей копии, git-ревизии и фейкового GitHub API, построенного из того же git-репо;
- GitHub: ref со `/` в URL, 401, 404, rate limit, ретраи 5xx, fallback на clone, ошибка чтения одного файла, токен не попадает в снапшот;
- база: имена файлов, запись без перезаписи, cache hit, `dirty`, неизвестная версия схемы;
- агрегация: последний снапшот репо, группировка, связь по `node_id`, 1000 снапшотов быстрее 2 с;
- TUI: экран репозиториев, переход в снапшот и назад, пустая база, группировка копий, скан из TUI (успех и ошибка), фильтр категорий;
- веб-UI: реальный HTTP-сервер в тестах — токен и cookie, `Host`, CSP, обход пути к снапшоту, XSS в теле скилла (raw HTML, `javascript:`, изображения), скан через API (успех, ошибка, один скан за раз, этапы), CSRF-условия для `POST`, `/api/skills`;
- веб-UI в браузере: `tests/test_web_e2e.py` запускает headless Chrome через DevTools Protocol (`tests/e2e/web.mjs`, Node 22+) и проходит 29 проверок: карточки, фильтры, панель скилла, группировка копий, восстановление состояния из URL, глобальный поиск, скан, rescan, ошибка скана, вёрстка 390 px, отсутствие ошибок консоли и нарушений CSP. Без Chrome или Node тест пропускается; в CI `SKILL_ATLAS_REQUIRE_E2E=1` превращает пропуск в ошибку;
- категории: правила по путям, включая ложные совпадения (`testing`, `latest`, `spec`, имя каталога скилла), старые снапшоты без категорий, cache miss при смене `detectors_version`;
- санитизация: unit-тесты и fuzz (`hypothesis`); TUI и `--plain` на контенте с ANSI, OSC 8 и bidi.

Ручная проверка: скан `JetBrains/kotlin` (111 487 файлов, Trees API обрезает листинг) за ~26 с, 6 скиллов. Скан `anthropics/skills` за ~3 с, 20 скиллов, 5 плагинов маркетплейса. Тула нашла реальные нарушения спеки: `description` длиннее 1024 символов у `claude-api` и `name`, не совпадающий с каталогом, у `template`.

Скан `JetBrains/MPS`: 114 записей, после группировки копий 41 строка (41 скилл лежит в `.agents/skills` и `.claude/skills`, 32 из них ещё и в `plugins/mcp-tools/resources/.../skills`).

Категории на реальных репо: `JetBrains/koog` — 2 project, 2 test (`integration-tests/src/jvmTest/resources`); `JetBrains/MPS` — 82 project, 32 bundled; `anthropics/skills` — 19 plugin, 1 template; `anthropics/claude-plugins-official` — 94 plugin, 3 example (`example-plugin`), 262 external.

Не реализовано в v1: GitHub Enterprise не проверен на живом инстансе; Windows не проверен.
