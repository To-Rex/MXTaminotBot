# MX Таъминотчи — Telegram Supplier Bot

**Production-ready multi-bot platform** — таъминотчилар (поставщики) учун Telegram бот + 1C интеграцияси ва web админ-панель.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.13+ |
| Web Framework | FastAPI |
| Telegram SDK | Aiogram 3 |
| ORM | SQLAlchemy (async) |
| Database | SQLite (via aiosqlite) |
| Migrations | Alembic |
| Templates | Jinja2 |
| HTTP Client | httpx |
| PDF | fpdf2 (акт сверка) |
| Session | Starlette SessionMiddleware (itsdangerous) |
| Server | Uvicorn |

## Architecture

```
├── run.py / app.py                 # Entry points
├── requirements.txt
├── alembic/                        # Migrations (async SQLAlchemy env)
├── docs/
│   └── 1C_SUPPLIER_API.md          # 1C HTTP-servis spetsifikatsiyasi
└── app/
    ├── main.py                     # FastAPI app, lifespan, middleware
    ├── config.py                   # Environment settings
    ├── database.py                 # Async engine & session
    ├── models.py                   # Bot, User (language, supplier bog'lanishi)
    ├── i18n.py                     # Ўзбекча / Русский bot matnlari
    ├── services/
    │   ├── supplier_api.py         # 1C supplier API klienti + mock fallback
    │   ├── mock_data.py            # 1C tayyor bo'lmaganda namunaviy ma'lumot
    │   ├── pdf.py                  # Акт сверка PDF generator
    │   ├── api_log.py              # /panel/api-logs uchun 1C so'rovlar jurnali
    │   ├── auth_api.py             # Panel login (tashqi auth API)
    │   ├── http_client.py          # Umumiy httpx klienti
    │   └── bot_manager.py          # Dynamic bot lifecycle manager
    ├── handlers/
    │   └── router.py               # Telegram handlers (TZ bo'limlari)
    └── web/
        ├── auth.py                 # Panel auth middleware, login/logout
        └── routes.py               # Admin panel CRUD (/panel)
```

## Telegram Bot — TZ funksiyalari

### 1. Рўйхатдан ўтиш ва авторизация
- `/start` → тил танлаш: **Ўзбекча / Русский** (кейин 🌐 Тил тугмаси орқали алмаштирилади)
- Телефон рақам (contact) → 1C `checkNumber` → таъминотчи аниқланади, Telegram аккаунт боғланади
- Таъминотчи фақат **ўз** маълумотларини кўради (ҳар бир 1C сўровида `supplier_id`)

### 2. Асосий меню
```
👤 Кабинет         💳 Баланс
💰 Тўловлар        📦 Берилган юклар
🔄 Юк қайтариш     🎁 Бонуслар
📄 Акт сверка      🌐 Тил
      🚪 Чиқиш
```
> Web-kabinet menyuda alohida tugma emas — Telegram'ning o'z «Web App» tugmasi orqali ochiladi.

| Бўлим | Функциялар |
|---|---|
| **👤 Кабинет** | номи, телефон, статус, ID, ҳамкорлик санаси + умумий кўрсаткичлар (юклар, тўловлар, бонус, баланс) — 1C `getClientInfo` (MX-Client-Bot билан умумий, тайёр) |
| **💰 Тўловлар** | рўйхат → деталлар (ҳужжат, сана, сумма, усул, ҳолат) → ✅ тасдиқлаш (1C га узатилади) |
| **📦 Берилган юклар** | рўйхат → детализация: товар номи, миқдори, суммаси, санаси |
| **🔄 Юк қайтариш** | рўйхат → деталлар (сабаб, товарлар) → ✅ тасдиқлаш → ҳолат |
| **🎁 Бонуслар** | ҳисобланган / фойдаланилган / қолган бонус + тарих |
| **💳 Баланс** | жорий баланс (мусбат — компания қарздор) |
| **📄 Акт сверка** | давр танлаш (жорий/ўтган ой, 3 ой, йил, ихтиёрий) → кўриш → **PDF юклаб олиш** |

### 1C интеграцияси ва mock режим
Барча маълумотлар `app/services/supplier_api.py` орқали 1C дан олинади
(`{base_url}/hs/supplier_bot/api/…`, spec: `docs/1C_SUPPLIER_API.md`).

1C да ҳали тайёр бўлмаган endpoint учун:
- `SUPPLIER_MOCK_FALLBACK=1` (default) — бот **намунавий маълумотлар** билан ишлайди, фойдаланувчига «Демо режим» белгиси кўрсатилади. Тасдиқлашлар mock ҳолатида ҳам ишлайди (runtime да сақланади).
- `SUPPLIER_MOCK_FALLBACK=0` — «Бу хизмат тез кунда ишга тушади» кўрсатилади.

Endpoint 1C да тайёр бўлгач бот ҳеч қандай ўзгаришсиз реал маълумотга ўтади. Барча 1C сўровлари `/panel/api-logs` да кўринади (kutilgan javob shakli bilan solishtirib).

### APIs Integrated (1C, per-bot credentials)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/hs/client_bot/api/checkNumber` | POST | **Login — MX-Client-Bot билан бир хил** (`APIService.register_device`, legacy `/hs/client/api/device` fallback). Тана: `{phoneNumber, chatID, botID}` — `botID` панелдаги бот рақами |
| `/hs/client_bot/api/getClientInfo` | GET | 👤 Кабинет профили (MX-Client-Bot билан умумий, 1C да тайёр) |
| `/hs/supplier_bot/api/getBalance` | GET | Жорий баланс |
| `/hs/supplier_bot/api/getPayments` | GET | Тўловлар рўйхати |
| `/hs/supplier_bot/api/confirmPayment` | POST | Тўловни тасдиқлаш (1C да қайд этилади) |
| `/hs/supplier_bot/api/getShipments` | GET | Берилган юклар (товарлар билан) |
| `/hs/supplier_bot/api/getReturns` | GET | Қайтаришлар рўйхати |
| `/hs/supplier_bot/api/confirmReturn` | POST | Қайтаришни тасдиқлаш |
| `/hs/supplier_bot/api/getBonuses` | GET | Бонуслар (ҳисобланган/фойдаланилган/қолган) |
| `/hs/supplier_bot/api/getAktSverka` | GET | Акт сверка (давр бўйича) |

## WebApp (Telegram Mini App + brauzer)

Bot bilan **100% bir xil funksionallik** — MX-Client-Bot dagi kabi arxitektura:

- **Kirish:** Telegram'ning **o'z «Web App» menyu tugmasi** (xabar maydoni yonida — `set_chat_menu_button` orqali har bir botga avtomatik o'rnatiladi, initData auth) yoki `/getsession` bergan shaxsiy havola bilan brauzerdan (30 kunlik sessiya)
- ⚠️ Telegram Mini App **HTTPS** talab qiladi: `WEBAPP_URL=https://<domen>` bo'lsagina menyu tugmasi o'rnatiladi. Lokal manzilda tugma o'rnatilmaydi (logda aniq ogohlantirish chiqadi) — bu holda `/getsession` havolasi bilan brauzerda oching. Lokal sinov uchun: `cloudflared tunnel --url http://localhost:8000`
- **Auth:** `X-Telegram-Init-Data` (HMAC tekshiruv) yoki `?session=<token>` — `app/web/web_app_auth.py`
- **Bo'limlar:** 👤 Кабинет · 💰 Тўловлар (tasdiqlash bilan) · 📦 Юклар · 🔄 Қайтаришлар (tasdiqlash) · 🎁 Бонуслар · 💳 Баланс · 📄 Акт сверка (davr + **PDF yuklab olish**)
- **Til:** uz/ru — botdagi til bilan sinxron (bazada saqlanadi). Demo rejimda namunaviy ma'lumot matnlari (tovar nomlari, omborlar, sabablar, izohlar) ham tanlangan tilda qaytadi
- **Dizayn:** Telegram temasiga moslashadi (dark/light), mobil tabbar + desktop sidebar
- SPA: `app/templates/webapp.html`, JSON API: `app/web/web_app_api.py` (`/webapp/api/*`)

Ishga tushirish uchun `WEBAPP_URL` env o'zgaruvchisini tashqi manzilga qo'ying (masalan `https://bot.mxsoft.uz`) — bot menyusida WebApp tugmasi avtomatik o'rnatiladi.

## Admin Panel

Access at `http://localhost:8000/panel` (login required — external auth API, faqat `SUPERADMIN`/`ADMIN`).

| Screen | Route | Description |
|--------|-------|-------------|
| **Login** | `/login` | Email + password → JWT token |
| **Bot list** | `/panel` | Barcha botlar, status, foydalanuvchi soni |
| **Create/Edit bot** | `/panel/bots/…` | Token, 1C `base_url` + login/parol |
| **Statistics** | `/panel/bots/{id}/stats` | Foydalanuvchilar |
| **API logs** | `/panel/api-logs` | 1C so'rov/javob jurnali (debug) |

## Getting Started

```bash
git clone <repo-url>
cd MX-taminot-Bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# migratsiyalar
alembic upgrade head

# run
python app.py
# Admin panel: http://localhost:8000/panel
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///app.db` | Database connection string |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Server bind |
| `LOG_LEVEL` | `INFO` | Logging level |
| `SUPPLIER_MOCK_FALLBACK` | `1` | 1C tayyor bo'lmagan endpoint uchun mock ma'lumot |
| `WEBAPP_URL` | *(bo'sh)* | WebApp tashqi manzili — **`https://<domen>`** bo'lsagina Telegram menyu tugmasi o'rnatiladi; bo'sh bo'lsa WebApp o'chiq |
| `AUTH_API_BASE` | … | Panel login uchun tashqi auth API |
| `SESSION_SECRET_KEY` | change me | Panel session imzosi |

## Extending

### Yangi 1C endpoint qo'shish
1. `docs/1C_SUPPLIER_API.md` ga spetsifikatsiya yozing
2. `app/services/supplier_api.py` ga metod (+ `EXPECTED_SHAPES`) va kerak bo'lsa `mock_data.py` ga mock qo'shing
3. `app/handlers/router.py` da handler yozing; matnlarni `app/i18n.py` ga ikkala tilda qo'shing

## License

Proprietary. All rights reserved.
