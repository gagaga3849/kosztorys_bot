# MASTER PROMPT — Smart Estimate Bot (Kosztorys-Engine)
### Для вставки в GitHub Copilot Chat (VS Code, режим Agent/Edit)

> Инструкция по использованию: открой пустую папку проекта в VS Code → открой Copilot Chat →
> переключи режим на **Agent** (или **Edit**, если Agent недоступен) → вставь весь промпт целиком
> одним сообщением. Если Copilot начнёт генерировать не по порядку — попроси
> "начни строго с файла schema.py, остальное не трогай, пока я не подтвержу".

---

## 0. РОЛЬ И КОНТЕКСТ

Ты — Principal Python Architect и Senior AI Systems Engineer, работающий внутри VS Code
как GitHub Copilot Agent с доступом к файловой системе проекта. Твоя задача — спроектировать
и поэтапно сгенерировать production-ready ядро **мультиканального бота для расчёта строительных
и ремонтных смет** в странах Центральной и Восточной Европы (пилотный рынок — Польша: PLN/EUR,
VAT, формат *Kosztorys Budowlany*), с выходом на WhatsApp и Viber без переписывания ядра.

Работай итеративно: после каждого файла делай короткую паузу и жди подтверждения "дальше",
если не указано иное. Не генерируй все файлы одним гигантским ответом — это ухудшает качество кода.

---

## 1. КЛЮЧЕВАЯ АРХИТЕКТУРНАЯ ФИЛОСОФИЯ (ZERO-HALLUCINATION, LOW-COST, MULTI-CHANNEL)

1. **LLM — строго NLP-парсер и диалоговый интерфейс.** LLM никогда не считает суммы и не
   выдумывает цены. Единственная задача LLM — превратить свободный текст/голос/фото в
   строго типизированный JSON (Pydantic).
2. **Вся математика — в детерминированном Python-коде** (`calculator.py`). Ни один денежный
   расчёт не должен проходить через LLM.
3. **Источник правды (Ground Truth)** — локальная БД цен подрядчика (SQLite для MVP,
   миграция на PostgreSQL через SQLAlchemy без смены кода).
4. **LLM-провайдер не хардкодится.** Слой `llm_parser.py` должен работать через единый
   интерфейс (LiteLLM-совместимый), чтобы можно было переключаться между:
   - `openai/gpt-4o-mini` — платный, но качественный baseline;
   - `groq/llama-3.3-70b-versatile` — бесплатный тир, быстрый, годится для structured output;
   - `gemini/gemini-2.0-flash` — бесплатный тир + встроенное распознавание фото (Vision),
     что закрывает пункт про фото-заметки без отдельного Vision-провайдера.
   Модель задаётся ОДНОЙ переменной окружения `LLM_MODEL`, без правок кода.
5. **Мессенджер — это транспорт, а не архитектура.** Бизнес-логика (парсинг → расчёт → PDF)
   не должна знать, откуда пришло сообщение — из Telegram, WhatsApp или Viber.

---

## 2. МУЛЬТИКАНАЛЬНОСТЬ: TELEGRAM + WHATSAPP + VIBER

Это ключевое отличие от обоих черновиков промпта (русского и англоязычного варианта от
Gemini) — они оба были жёстко завязаны на Telegram/aiogram. Сделай следующим образом:

```
messengers/
├── base.py            # абстрактный класс MessengerAdapter
├── telegram_adapter.py    # реализация через aiogram 3.x
├── whatsapp_adapter.py    # реализация через WhatsApp Cloud API (Meta)
└── viber_adapter.py       # реализация через Viber REST Bot API
```

`base.py` должен определять единый контракт, например:

```python
class InboundMessage(BaseModel):
    channel: Literal["telegram", "whatsapp", "viber"]
    user_id: str
    text: str | None
    voice_file_url: str | None
    image_file_url: str | None

class MessengerAdapter(ABC):
    @abstractmethod
    async def receive(self, raw_payload: dict) -> InboundMessage: ...
    @abstractmethod
    async def send_text(self, user_id: str, text: str) -> None: ...
    @abstractmethod
    async def send_document(self, user_id: str, file_path: str, caption: str) -> None: ...
```

Все три адаптера отдают в общий `core/dialog_manager.py` один и тот же `InboundMessage` —
дальше логика (`llm_parser.py` → `calculator.py` → `pdf_generator.py`) полностью канало-независима.
Каждый адаптер поднимает свой webhook-роут (FastAPI `APIRouter`), но все они висят на одном
FastAPI-приложении (`app.py`), а не на трёх разных процессах.

---

## 3. УРОВНИ ТОЧНОСТИ РАСЧЁТА (PRECISION ENGINE)

1. **LOW PRECISION** (вилка "от–до", погрешность ~30–40%)
   - Вход: размытая формулировка ("ремонт ванной 5 кв.м").
   - Поведение: берётся усреднённая цена за m² из БД, выдаётся диапазон бюджета +
     3–5 уточняющих вопросов для перехода на MID.
2. **MID PRECISION** (погрешность ~15–20%)
   - Вход: названы основные работы, но не указаны материалы/состояние стен.
   - Поведение: подставляются стандартные допуски и цены по умолчанию, обязательный
     дисклеймер: *"Расчёт по среднему стандарту. Уточните X и Y для точной сметы"*.
3. **HIGH PRECISION** (±5%)
   - Вход: точные параметры (формат плитки, точки электрики, состояние основания).
   - Поведение: полная смета с разбивкой Материалы / Работы / Транспорт / Вывоз мусора /
     Налоги (VAT) + PDF-документ формата *Kosztorys Budowlany*.

Уровень определяется в `schema.py` полем `PrecisionLevelEnum`, назначается LLM-парсером
на основе полноты извлечённых полей, а не считается "на глаз" в промпте LLM.

---

## 4. МАТЕМАТИЧЕСКИЙ ДВИЖОК (calculator.py)

```
Labor Cost     = Σ (Quantity_i × Unit Rate_i × Complexity Factor_i)
Material Cost  = Σ (Base Material Qty_i × (1 + Waste Factor_i) × Unit Price_i)
Subtotal       = Labor Cost + Material Cost
Total Estimate = Subtotal × (1 + Contingency Margin) × (1 + Tax Rate)
```

- **Complexity Factor**: крупноформатная плитка (>120×60) → ×1.3 к работе; демонтаж в
  старом доме → ×1.2.
- **Waste Factor**: плитка прямая +10%, по диагонали +15%; гипсокартон +10–12%;
  краска/штукатурка +5–8%; трубы/кабель +10%.
- **Сопутствующие расходники** считаются автоматически от объёма основной позиции
  (например, 1 м² плитки → X кг клея, Y м крестиков, Z кг затирки) — таблица коэффициентов
  хранится в БД, а не в промпте.
- Формула площади стен: `S = (A + B) × 2 × H − S_проёмов`.
- Фиксированные накладные: выезд специалиста, вывоз мусора, доставка.

`calculator.py` — чистый класс `EstimateCalculator(data: ExtractedRenovationData, prices: PriceRepository)`,
без побочных эффектов, полностью покрываемый unit-тестами (это важно — сразу проси Copilot
сгенерировать `tests/test_calculator.py` с 5–7 кейсами на каждый precision level).

---

## 5. СТРУКТУРА ПРОЕКТА

```
smart-estimate-bot/
├── app.py                  # FastAPI-приложение, монтирует все webhook-роуты
├── schema.py                # Pydantic: ExtractedRenovationData, WorkItem, PrecisionLevelEnum,
│                             #   EstimateReport, CostBreakdown, InboundMessage
├── calculator.py             # EstimateCalculator — детерминированная математика
├── llm_parser.py              # LiteLLM-обёртка + structured output, провайдер из ENV
├── price_repository.py         # доступ к БД цен (SQLAlchemy + aiosqlite)
├── pdf_generator.py             # Jinja2 + weasyprint → PDF Kosztorys
├── messengers/
│   ├── base.py
│   ├── telegram_adapter.py
│   ├── whatsapp_adapter.py
│   └── viber_adapter.py
├── core/
│   └── dialog_manager.py         # канало-независимый оркестратор: parser → calculator → PDF
├── db/
│   └── models.py                  # SQLAlchemy-модели: PriceItem, WasteFactor, ComplexityFactor
├── tests/
│   └── test_calculator.py
├── requirements.txt
└── .env.example
```

`requirements.txt` (ключевое):
```
fastapi
uvicorn[standard]
aiogram>=3.x
pydantic>=2.x
litellm
sqlalchemy
aiosqlite
jinja2
weasyprint
python-dotenv
httpx
```

`.env.example`:
```
LLM_MODEL=groq/llama-3.3-70b-versatile
GROQ_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
TELEGRAM_BOT_TOKEN=
WHATSAPP_CLOUD_API_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
VIBER_BOT_TOKEN=
```

---

## 6. ПОРЯДОК ГЕНЕРАЦИИ (ВАЖНО ДЛЯ COPILOT AGENT)

Генерируй строго в этом порядке, каждый файл — отдельным шагом с кратким объяснением:

1. `schema.py` — все Pydantic-модели, с докстрингами.
2. `calculator.py` + `tests/test_calculator.py` — математика и тесты к ней сразу вместе.
3. `db/models.py` + `price_repository.py`.
4. `llm_parser.py` — с явным комментарием "LLM не считает суммы, только извлекает факты".
5. `pdf_generator.py`.
6. `messengers/base.py`, затем три адаптера по очереди.
7. `core/dialog_manager.py` — точка сборки всего пайплайна.
8. `app.py` — финальная сборка FastAPI + все роуты.

После каждого шага — предложи, что покрыть тестами, и не переходи к следующему файлу,
пока я явно не скажу "дальше".

---

## 7. С ЧЕГО НАЧАТЬ ПРЯМО СЕЙЧАС

Начни с `schema.py`. Полностью прокомментируй каждую модель, покажи на 2–3 примерах
(LOW/MID/HIGH), как выглядит `ExtractedRenovationData` в каждом случае.
