# MX Таъминотчи — 1C HTTP-servis API spetsifikatsiyasi

Telegram bot (taminotchining shaxsiy kabineti) uchun 1C:Enterprise tomonida yaratilishi kerak bo'lgan HTTP-servis.

Bot **barcha** endpointlarga ulangan (`app/services/supplier_api.py`) va **barcha ma'lumotni faqat 1C dan** oladi — namunaviy (mock/demo) ma'lumot yo'q.

1C javob bermasa (404 / bo'sh javob / 5xx / tarmoq xatosi / noto'g'ri shakl) foydalanuvchiga xatolik haqida xabar ko'rsatiladi:
«❌ Маълумот олинмади — 1C билан алоқа йўқ ёки хизмат жавоб бермаяпти», WebApp da esa `503 {"code": "SERVICE_UNAVAILABLE"}`.
Xatolikning **to'liq tafsiloti** (URL, so'rov tanasi, kelgan javob, kutilgan shakl bilan taqqoslash) admin paneldagi `/panel/api-logs` sahifasida ko'rinadi.

Bugungi kunda tayyor va botga ulangan (MX-Client-Bot bilan umumiy, `hs/client_bot/api/` ostida): **`checkNumber`** (1-bo'lim), **`getClientInfo`** (2-bo'lim). Qolganlari — `hs/supplier_bot/api/` ostida yaratilishi kerak.

> **Muhim:** 1C hozir mavjud bo'lmagan GET manzillarga ham `200` + bo'sh tana qaytarishi mumkin. Bot buni «tayyor emas» deb hisoblaydi (javob shakli tekshiriladi). Endpoint tayyor bo'lganda javob aynan shu hujjatdagi shaklda bo'lishi shart. Har bir 1C so'rovi/javobi admin paneldagi `/panel/api-logs` sahifasida kutilgan shakl bilan yonma-yon ko'rinadi — farqni shu yerdan tekshirish qulay.

---

## 0. Umumiy qoidalar

### 0.1 Ulanish

| Narsa | Qiymat |
|---|---|
| Bazaviy URL (yangi) | `{base_url}/hs/supplier_bot/api/` — masalan `http://server.mxsoft.uz/demo/hs/supplier_bot/api/`. `base_url` har bir bot uchun admin panelda saqlanadi |
| Bazaviy URL (umumiy, tayyor) | `{base_url}/hs/client_bot/api/` — faqat `checkNumber` va `getClientInfo` uchun (MX-Client-Bot bilan bitta endpoint) |
| Autentifikatsiya | **HTTP Basic Auth** — panelda kiritilgan `1C login` / `1C password`. Masalan `bot_api:123` → sarlavha `Authorization: Basic Ym90X2FwaToxMjM=` |
| Format | JSON, `Content-Type: application/json; charset=utf-8` — so'rov tanasi ham, javob ham |
| Kodlash | UTF-8 (kirill matnlar to'g'ri ko'rinishi uchun) |
| Timeout | Bot 30 soniya kutadi; javob shundan tez bo'lishi kerak |
| Retry | GET so'rovlar vaqtinchalik xatoda (tarmoq, 5xx, bo'sh tana) bir marta qayta uriniladi; POST lar takrorlanmaydi |

### 0.2 Ma'lumot tiplari

| Tip | Format | Misol |
|---|---|---|
| `date` | `YYYY-MM-DD` | `"2026-08-20"` |
| `datetime` | `YYYY-MM-DDTHH:MM:SS` (Toshkent vaqti) | `"2026-08-20T14:52:10"` |
| `money` | son (float yoki int), **so'mda**, tiyinsiz, matn EMAS | `5400000` yoki `5400000.0` |
| `id` | butun son (1C ichki kod) | `70123` |
| `currency` | valyuta kodi, katta harflarda (ISO): `UZS` · `USD` · `EUR` | `"USD"` |
| `string` | UTF-8 matn | `"TL-260812-101"` |
| `bool` | `true` / `false` | |
| bo'sh qiymat | `null` (majburiy bo'lmagan maydonlar uchun) | |
| bo'sh ro'yxat | `[]` (`null` emas) | |

### 0.3 Javob va xato formati

- Muvaffaqiyat: HTTP **200**, javob to'g'ridan-to'g'ri obyekt yoki ro'yxat (o'ramsiz).
- Xato: HTTP **4xx/5xx** + tana:

```json
{ "error": { "code": "SUPPLIER_NOT_FOUND", "message": "Таъминотчи топилмади" } }
```

| Kalit | Tip | Izoh |
|---|---|---|
| `error.code` | string | mashina o'qiydigan kod (quyidagi jadval) — bot shu bo'yicha qaror qiladi |
| `error.message` | string | inson o'qiydigan izoh (bot uni foydalanuvchiga ko'rsatadi) |

| HTTP | `error.code` | Qachon |
|---|---|---|
| 401 | `UNAUTHORIZED` | Basic Auth noto'g'ri |
| 400 | `VALIDATION_ERROR` | parametr yo'q yoki noto'g'ri (`message` da qaysi) |
| 404 | `SUPPLIER_NOT_FOUND` | `supplier_id` (yoki telefon) topilmadi |
| 404 | `PAYMENT_NOT_FOUND` | `payment_id` topilmadi yoki bu taminotchiniki emas |
| 404 | `RETURN_NOT_FOUND` | `return_id` topilmadi yoki bu taminotchiniki emas |
| 400 | `ALREADY_CONFIRMED` | hujjat allaqachon tasdiqlangan (yoki shunchaki joriy holatni 200 bilan qaytaring — 5.2/6.2 idempotentlik) |
| 500 | `INTERNAL_ERROR` | 1C ichki xatosi |

> **Xavfsizlik (TZ 4):** har bir so'rovda `supplier_id` tekshiriladi — taminotchi faqat **o'z** hujjatlarini ko'radi va tasdiqlaydi. Boshqa taminotchining hujjati so'ralsa → `404`. Barcha tasdiqlash operatsiyalari 1C da qayd etiladi (kim, qachon, qaysi kanaldan).

### 0.4 Enum qiymatlar (aynan shu matnlar, kichik harf, lotin)

| Maydon | Qiymat | Ma'nosi |
|---|---|---|
| To'lov / qaytarish `status` | `pending` | taminotchi tasdig'i kutilmoqda |
| | `confirmed` | taminotchi tasdiqlagan |
| To'lov `status` (qo'shimcha) | `cancelled` | bekor qilingan |
| To'lov `method` | `cash` | naqd |
| | `transfer` | bank o'tkazmasi |
| | `card` | karta / terminal |
| | `other` | boshqa |
| Bonus `kind` | `accrued` | hisoblangan |
| | `used` | foydalanilgan |
| Qaytarish `reason_code` | `expiry` · `damaged` · `excess` · `other` | muddat · shikast · ortiqcha · boshqa |
| `source` | `telegram_bot` | so'rov Telegram botdan keldi |
| | `webapp` | so'rov WebApp (Mini App / brauzer kabineti) dan keldi |

### 0.5 Bot javobni qanday o'qiydi (muhim!)

Bot javobga bardoshli, lekin **standart qiymatlari** bor — 1C maydonni tushirib qoldirsa nima bo'lishini bilib qo'ying:

| Holat | Bot nima qiladi |
|---|---|
| To'lovda `status` yo'q yoki notanish | **`confirmed`** deb hisoblaydi (tasdiqlash tugmasi chiqmaydi!) — `pending` ni doim aniq yuboring |
| Qaytarishda `status` yo'q yoki notanish | **`pending`** deb hisoblaydi (tasdiqlash tugmasi chiqadi) |
| `method` yo'q yoki notanish | `other` → foydalanuvchiga «Бошқа» deb ko'rsatiladi |
| Hujjatda `total` yo'q | `products[].sum` yig'indisi hisoblanadi |
| Tovarda `sum` yo'q | `price × qty` hisoblanadi |
| Sana formati boshqacha | `YYYY-MM-DD`, `DD.MM.YYYY`, `YYYYMMDD` va ISO datetime qabul qilinadi |
| Ro'yxat o'ralgan bo'lsa (`{"items": [...]}`) | `items` / `data` / `list` / `rows` kalitlari ochib olinadi (lekin **o'ramsiz ro'yxat** afzal) |
| Obyekt bitta elementli ro'yxatda kelsa (`[{…}]`) | ochib olinadi |
| Hujjatda ko'rsatilmagan qo'shimcha maydonlar | e'tiborsiz qoldiriladi — javobga qo'shimcha maydon qo'shish xavfsiz |

> Javob shakli umuman mos kelmasa (masalan ro'yxat o'rniga obyekt) bot foydalanuvchiga xatolik ko'rsatadi, `/panel/api-logs` da esa kelgan javob kutilgan shakl bilan yonma-yon chiqadi.

---

## 1. Taminotchini aniqlash — `POST checkNumber` ✅ tayyor (MX-Client-Bot bilan umumiy)

Telefon raqami bo'yicha taminotchini topadi va Telegram `chat_id` ni unga bog'laydi. Bot login qismida chaqiriladi. **MX-Client-Bot dagi login bilan bir xil** — 1C dagi tayyor endpoint ishlatiladi; topilmasa legacy `/hs/client/api/device` ga fallback qilinadi.

### So'rov
```http
POST /hs/client_bot/api/checkNumber
Authorization: Basic Ym90X2FwaToxMjM=
Content-Type: application/json

{ "phoneNumber": 998901234567, "chatID": "66540046", "botID": 1 }
```

| Kalit | Tip | Majburiy | Izoh |
|---|---|---|---|
| `phoneNumber` | number | ha | `+`, bo'shliq va chiziqchasiz, `998` bilan boshlanadi |
| `chatID` | string | ha | Telegram chat/user id — 1C taminotchi kartochkasiga yozib qo'yadi (keyin xabar yuborish uchun) |
| `botID` | number | ha | admin paneldagi bot raqami — bitta 1C bazasiga bir nechta bot ulanganda taminotchi qaysi bot orqali kirganini aniqlash uchun. 1C uni kartochkaga saqlaydi (keyinchalik 1C → bot xabarlari kelishilganda ham shu raqam ishlatiladi) |

### Javob 200
```json
{ "id": 70123, "name": "MCHJ \"Taminot Trade\"" }
```

| Kalit | Tip | Izoh |
|---|---|---|
| `id` | id | taminotchining 1C dagi kodi — bot uni `supplier_id` sifatida saqlaydi va **keyingi barcha so'rovlarda** yuboradi |
| `name` | string | taminotchi nomi — salomlashishda va kabinetda ko'rsatiladi |

Topilmasa: `404` + `SUPPLIER_NOT_FOUND` (yoki bo'sh `{}` — bot ikkalasini ham "topilmadi" deb qabul qiladi).

Xuddi shu so'rov **WebApp** dagi ro'yxatdan o'tishda ham yuboriladi (`botID` bilan birga) — 1C tomonida farqi yo'q.

**Legacy fallback:** `checkNumber` `404` qaytarsa, bot bir marta eski manzilga uradi:
`POST /hs/client/api/device` — tanasi `{"phone_number": <int>, "chat_id": "<str>"}` (bu yerda `botID` yo'q).
Yangi bazalarda bu kerak emas — faqat eski o'rnatmalar bilan mos kelish uchun saqlangan.

> **Diqqat:** shu raqam mijoz sifatida ham ro'yxatda bo'lsa, bu bot uchun **taminotchi** sifatida tekshirilishi kerak. Agar bitta `checkNumber` ikkala rolni ham qaytarsa, 1C tomonida taminotchi ekanini ajratib berish kerak bo'ladi (kelishiladi).

---

## 2. Kabinet profili — `GET getClientInfo` ✅ tayyor (MX-Client-Bot bilan umumiy)

«👤 Кабинет» sahifasi. Bot 1C dagi tayyor endpointdan foydalanadi.

### So'rov
```http
GET /hs/client_bot/api/getClientInfo?client_id=70123
```

| Parametr | Tip | Majburiy | Izoh |
|---|---|---|---|
| `client_id` | id | ha | `checkNumber` qaytargan `id` (taminotchi kodi) |

### Javob 200
```json
{
  "client_id": 70123,
  "name": "MCHJ \"Taminot Trade\"",
  "phone": "998901234567",
  "status": "Фаол таъминотчи",
  "registered_at": "2025-03-14"
}
```

Bot quyidagi maydonlarni ishlatadi (**qolgan maydonlar bo'lsa e'tiborsiz qoldiriladi** — MX-Client-Bot javobidagi mijoz maydonlari xalaqit bermaydi):

| Kalit | Tip | Izoh |
|---|---|---|
| `client_id` | id | taminotchi kodi (so'rovdagi bilan bir xil) |
| `name` | string | taminotchi nomi — kabinetda «Номи» qatori |
| `phone` | string | telefon, `998…` |
| `status` | string | status matni (masalan «Фаол таъминотчи», «VIP») — kabinetda belgi |
| `registered_at` | date | hamkorlik boshlangan sana |

Kabinetdagi qolgan ko'rsatkichlarni (yuklar, to'lovlar, bonus, balans) bot 3–7-bo'limlardagi endpointlardan o'zi yig'adi — `getClientInfo` ni o'zgartirish shart emas.

---

## 3. Balans — `GET getBalance` (TZ 2.5)

«💳 Баланс» — taminotchining joriy o'zaro hisob-kitob qoldig'i.

### So'rov
```http
GET /hs/supplier_bot/api/getBalance?supplier_id=70123
```

| Parametr | Tip | Majburiy | Izoh |
|---|---|---|---|
| `supplier_id` | id | ha | |

### Javob 200 — **har bir valyuta uchun alohida qator** (ro'yxat)
```json
[
  { "balance": 19745664.6, "currency": "UZS", "as_of": "2026-08-27T13:38:35" },
  { "balance": -45539.92,  "currency": "USD", "as_of": "2026-08-27T13:38:35" }
]
```

| Kalit | Tip | Izoh |
|---|---|---|
| `balance` | money | **musbat** — kompaniya taminotchiga qarzdor; **manfiy** — taminotchi kompaniyaga qarzdor. Bot har bir valyuta uchun 🟢/🔴 ko'rsatadi |
| `currency` | string | valyuta kodi: `UZS`, `USD`, `EUR`… (katta harflarda) |
| `as_of` | datetime | qoldiq hisoblangan vaqt — «Ҳолат санаси» |

**Bot qanday ko'rsatadi:** barcha valyutalar ro'yxat bo'lib chiqadi; **asosiy** valyuta — `UZS`
(bo'lmasa ro'yxatdagi birinchisi) va u birinchi turadi. Kabinetdagi qisqa qatorda ham
hammasi ko'rinadi: `19 745 665 сўм · -45 539,92 USD`.
`UZS` butun songacha yaxlitlanadi, boshqa valyutalarda tiyin/sent ko'rsatiladi.

> Eski shakl — bitta obyekt `{ "balance": …, "currency": …, "as_of": … }` — ham qabul qilinadi
> (bot uni bitta elementli ro'yxat deb hisoblaydi), shuning uchun o'tish bosqichida ikkalasi ham ishlaydi.
> Bo'sh ro'yxat `[]` esa xato deb qabul qilinadi — hech bo'lmasa bitta valyuta qatori qaytsin.

> Tekshiruv: `UZS` qatoridagi `balance` = joriy sanagacha bo'lgan akt sverkaning `closing_balance` i bilan mos bo'lishi kerak (8-bo'lim).

---

## 4. Berilgan yuklar — `GET getShipments` (TZ 2.2)

«📦 Берилган юклар» — taminotchi kompaniyaga yetkazib bergan yuklar (kirim hujjatlari). Har bir yukda tovar nomi, miqdori, summasi va sanasi bo'lishi shart (TZ talabi).

### So'rov
```http
GET /hs/supplier_bot/api/getShipments?supplier_id=70123
```

| Parametr | Tip | Majburiy | Izoh |
|---|---|---|---|
| `supplier_id` | id | ha | |

### Javob 200 — ro'yxat (yangi → eski)
```json
[
  {
    "shipment_id": 100000002,
    "doc_number": "YT-2608-0011",
    "date": "2026-08-27",
    "warehouse": "Асосий омбор",
    "total": 46800,
    "cry": "USD",
    "products": [
      { "product_id": 4, "name": "Авалон Кондиционер 32", "qty": 100, "price": 450, "sum": 45000 },
      { "product_id": 3, "name": "Телевизор",             "qty": 15,  "price": 120, "sum": 1800 }
    ]
  }
]
```

| Kalit | Tip | Izoh |
|---|---|---|
| `shipment_id` | id | kirim hujjati kodi — bot detallar sahifasida ishlatadi |
| `doc_number` | string | inson o'qiydigan hujjat raqami — hamma joyda ko'rsatiladi |
| `date` | date | yuk berilgan (kirim qilingan) sana |
| `warehouse` | string | qaysi omborga qabul qilingan (bo'lmasa `""`) |
| `total` | money | hujjat jami summasi (`products[].sum` yig'indisi) |
| **`cry`** | string | **hujjat valyutasi**: `UZS`, `USD`, `EUR`… Berilmasa `UZS` deb hisoblanadi. `currency` nomi bilan yuborilsa ham qabul qilinadi |
| `products[]` | list | tovarlar (quyida) |

> **Valyuta hujjat darajasida:** `price` va `sum` shu hujjatning `cry` sida hisoblanadi —
> tovar ichida alohida valyuta ko'rsatish shart emas.
>
> **Bot qanday jamlaydi:** turli valyutadagi hujjatlar **qo'shilmaydi**, har biri alohida chiqadi —
> `Жами: 150 000 000 сўм · 46 800 USD`. `UZS` butun songacha yaxlitlanadi, boshqa valyutalarda sent ko'rsatiladi.
>
> Xuddi shu `cry` maydoni **`getReturns`** va **`getPaymentsSupplier`** javoblarida ham qabul qilinadi
> (ixtiyoriy, berilmasa `UZS`) — 1C da valyutali qaytarish/to'lov paydo bo'lsa, bot uni o'zi to'g'ri ko'rsatadi.

`products[]` — har bir element:

| Kalit | Tip | Izoh |
|---|---|---|
| `product_id` | id | tovar kodi |
| `name` | string | tovar nomi |
| `qty` | number | miqdor |
| `price` | money | **birlik narxi** — bot uni hujjat tafsilotida ko'rsatadi: «Авалон Кондиционер 32 — 100 × 450 USD = 45 000 USD». Shuning uchun to'ldirilgan bo'lsin (0 bo'lsa faqat miqdor va summa chiqadi) |
| `sum` | money | `price × qty` |

---

## 5. To'lovlar (TZ 2.1)

### 5.1 Ro'yxat — `GET getPaymentsSupplier`

«💰 Тўловлар» — kompaniya taminotchiga qilgan to'lovlar. `pending` holatdagilar taminotchi tasdig'ini kutadi («men bu pulni oldim»).

#### So'rov
```http
GET /hs/supplier_bot/api/getPaymentsSupplier?supplier_id=70123
```

| Parametr | Tip | Majburiy | Izoh |
|---|---|---|---|
| `supplier_id` | id | ha | |

#### Javob 200 — ro'yxat (yangi → eski)
```json
[
  {
    "payment_id": 90101,
    "doc_number": "TL-260812-101",
    "date": "2026-08-12",
    "amount": 5400000,
    "method": "transfer",
    "status": "pending",
    "confirmed_at": null,
    "note": "Юк YT-2608-0011 учун тўлов"
  },
  {
    "payment_id": 90100,
    "doc_number": "TL-260805-100",
    "date": "2026-08-05",
    "amount": 3200000,
    "method": "cash",
    "status": "confirmed",
    "confirmed_at": "2026-08-06T10:12:00",
    "note": "Қисман тўлов"
  }
]
```

| Kalit | Tip | Izoh |
|---|---|---|
| `payment_id` | id | to'lov hujjati kodi — `confirmPayment` da ishlatiladi |
| `doc_number` | string | hujjat raqami — foydalanuvchiga ko'rsatiladi |
| `date` | date | to'lov sanasi |
| `amount` | money | to'lov summasi |
| `method` | enum | `cash` / `transfer` / `card` / `other` (0.4-bo'lim) |
| `status` | enum | `pending` (tasdiqlash kutilmoqda) / `confirmed` / `cancelled` |
| `confirmed_at` | datetime \| null | taminotchi tasdiqlagan vaqt (`pending` da `null`) |
| `note` | string | izoh — qaysi yuk/shartnoma uchun ekani |

### 5.2 Tasdiqlash — `POST confirmPayment` (TZ 3: tasdiqlash 1C ga uzatiladi)

Taminotchi botda «✅ Тўловни тасдиқлаш» tugmasini bosganda chaqiriladi. 1C to'lov hujjatiga tasdiq belgisini qo'yadi (kim, qachon, qaysi kanaldan).

#### So'rov
```http
POST /hs/supplier_bot/api/confirmPayment
{
  "supplier_id": 70123,
  "payment_id": 90101,
  "chat_id": "66540046",
  "source": "telegram_bot"
}
```

| Kalit | Tip | Majburiy | Izoh |
|---|---|---|---|
| `supplier_id` | id | ha | boshqa taminotchining to'lovi bo'lsa → `404 PAYMENT_NOT_FOUND` |
| `payment_id` | id | ha | `getPaymentsSupplier` dagi kod |
| `chat_id` | string | yo'q | kim tasdiqlagani (Telegram id) — auditlash uchun |
| `source` | enum | yo'q | `telegram_bot` (botdan) yoki `webapp` (Mini App / brauzer kabinetidan) — 1C qaysi kanaldan tasdiqlanganini qayd etsin |

#### Javob 200
```json
{ "success": true, "payment_id": 90101, "status": "confirmed", "confirmed_at": "2026-08-20T12:05:00" }
```

| Kalit | Tip | Izoh |
|---|---|---|
| `success` | bool | `true` — qayd etildi |
| `payment_id` | id | to'lov kodi |
| `status` | enum | `confirmed` |
| `confirmed_at` | datetime | tasdiq vaqti — botda ko'rsatiladi |

**Idempotentlik:** bir xil `payment_id` ikkinchi marta kelsa — qayta o'zgartirilmasin, o'sha `confirmed` holat va asl `confirmed_at` qaytsin (yoki `400 ALREADY_CONFIRMED` — bot ikkalasini ham to'g'ri qabul qiladi, lekin 200 afzal).

---

## 6. Yuk qaytarish (TZ 2.3)

### 6.1 Ro'yxat — `GET getReturns`

«🔄 Юк қайтариш» — kompaniya taminotchiga qaytargan yuklar. `pending` holatdagilar taminotchi tasdig'ini kutadi («men bu yukni qabul qilib oldim»).

#### So'rov
```http
GET /hs/supplier_bot/api/getReturns?supplier_id=70123
```

| Parametr | Tip | Majburiy | Izoh |
|---|---|---|---|
| `supplier_id` | id | ha | |

#### Javob 200 — ro'yxat (yangi → eski)
```json
[
  {
    "return_id": 55201,
    "doc_number": "QT-2608-201",
    "date": "2026-08-14",
    "status": "pending",
    "confirmed_at": null,
    "reason": "Qadoq shikastlangan",
    "reason_code": "damaged",
    "total": 570000,
    "products": [
      { "product_id": 1001, "name": "Un oliy nav 50kg", "qty": 2, "price": 285000, "sum": 570000 }
    ]
  }
]
```

| Kalit | Tip | Izoh |
|---|---|---|
| `return_id` | id | qaytarish hujjati kodi — `confirmReturn` da ishlatiladi |
| `doc_number` | string | hujjat raqami |
| `date` | date | qaytarish sanasi |
| `status` | enum | `pending` / `confirmed` |
| `confirmed_at` | datetime \| null | taminotchi tasdiqlagan vaqt |
| `reason` | string | sabab matni — foydalanuvchiga ko'rsatiladi |
| `reason_code` | string | `expiry` / `damaged` / `excess` / `other` (0.4-bo'lim) |
| `total` | money | qaytarish jami summasi |
| `products[]` | list | 4-bo'limdagi `products` bilan bir xil tuzilma |

### 6.2 Tasdiqlash — `POST confirmReturn`

Taminotchi «✅ Қайтаришни тасдиқлаш» tugmasini bosganda chaqiriladi.

#### So'rov
```http
POST /hs/supplier_bot/api/confirmReturn
{
  "supplier_id": 70123,
  "return_id": 55201,
  "chat_id": "66540046",
  "source": "telegram_bot"
}
```

| Kalit | Tip | Majburiy | Izoh |
|---|---|---|---|
| `supplier_id` | id | ha | boshqa taminotchiniki bo'lsa → `404 RETURN_NOT_FOUND` |
| `return_id` | id | ha | |
| `chat_id` | string | yo'q | auditlash uchun |
| `source` | enum | yo'q | `telegram_bot` (botdan) yoki `webapp` (Mini App / brauzer kabinetidan) — 1C qaysi kanaldan tasdiqlanganini qayd etsin |

#### Javob 200
```json
{ "success": true, "return_id": 55201, "status": "confirmed", "confirmed_at": "2026-08-20T12:05:00" }
```

Idempotent (5.2 kabi).

---

## 7. Bonuslar — `GET getBonuses` (TZ 2.4)

«🎁 Бонуслар» — hisoblangan, foydalanilgan va qolgan bonus + tarix.

### So'rov
```http
GET /hs/supplier_bot/api/getBonuses?supplier_id=70123
```

| Parametr | Tip | Majburiy | Izoh |
|---|---|---|---|
| `supplier_id` | id | ha | |

### Javob 200
```json
{
  "accrued": 820000,
  "used": 300000,
  "remaining": 520000,
  "items": [
    { "bonus_id": 7701, "date": "2026-08-10", "kind": "accrued", "amount": 164000, "note": "Юк YT-2608-0011 бўйича 2% бонус" },
    { "bonus_id": 7790, "date": "2026-08-08", "kind": "used",    "amount": 300000, "note": "Ўзаро ҳисоб-китобда фойдаланилди" }
  ]
}
```

| Kalit | Tip | Izoh |
|---|---|---|
| `accrued` | money | jami hisoblangan bonus — «Ҳисобланган бонус» |
| `used` | money | foydalanilgan — «Фойдаланилган» |
| `remaining` | money | qolgan — «Қолган бонус» |
| `items[]` | list | tarix (yangi → eski) |
| `items[].bonus_id` | id | yozuv kodi |
| `items[].date` | date | sana |
| `items[].kind` | enum | `accrued` (➕) / `used` (➖) |
| `items[].amount` | money | summa |
| `items[].note` | string | izoh (qaysi yuk bo'yicha va h.k.) |

> Tekshiruv: `remaining = accrued − used`; `accrued` = `items` dagi `accrued` yig'indisi, `used` = `used` yig'indisi.

---

## 8. Valyutalar ro'yxati — `GET getcry`

«📄 Акт сверка» bosilganda bot **avval valyutani so'raydi** — ro'yxat shu endpointdan olinadi.

### So'rov
```http
GET /hs/supplier_bot/api/getcry
```
Parametrsiz.

### Javob 200
```json
[
  { "id": 1, "name": "USD" },
  { "id": 2, "name": "UZS" }
]
```

| Kalit | Tip | Izoh |
|---|---|---|
| `id` | id | valyuta kodi — `getAktSverka` ga `cry_id` sifatida yuboriladi |
| `name` | string | valyuta nomi: `UZS`, `USD`, `EUR`… (katta harflarda) |

Bot `UZS` ni ro'yxat boshiga qo'yadi. Valyuta bitta bo'lsa — tanlash o'tkazib yuboriladi.
Endpoint tayyor bo'lmasa bot valyuta tanlashsiz davom etadi (`cry_id` yuborilmaydi).

---

## 9. Akt sverka — `GET getAktSverka` (TZ 2.6)

«📄 Акт сверка» — tanlangan davr bo'yicha o'zaro hisob-kitob. Bot ma'lumotni ekranda ko'rsatadi va **PDF ni o'zi yasaydi** (`app/services/pdf.py`) — 1C dan faqat shu JSON kerak.

### So'rov
```http
GET /hs/supplier_bot/api/getAktSverka?supplier_id=70123&date_from=2026-08-01&date_to=2026-08-31&cry_id=1
```

| Parametr | Tip | Majburiy | Izoh |
|---|---|---|---|
| `supplier_id` | id | ha | |
| `date_from` | date | ha | davr boshi (shu kun kiradi) |
| `date_to` | date | ha | davr oxiri (shu kun kiradi) |
| **`cry_id`** | id | yo'q | foydalanuvchi tanlagan valyuta (`getcry` dagi `id`). Berilmasa 1C o'z standart valyutasida qaytaradi. **Barcha summalar shu valyutada bo'lishi kerak** |

### Javob 200
```json
{
  "supplier_id": 70123,
  "name": "MCHJ \"Taminot Trade\"",
  "date_from": "2026-08-01",
  "date_to": "2026-08-31",
  "opening_balance": 4300000,
  "total_debit": 8200000,
  "total_credit": 5400000,
  "closing_balance": 7100000,
  "rows": [
    { "date": "2026-08-10", "doc": "YT-2608-0011",  "note": "Юк берилди",     "debit": 8200000, "credit": 0 },
    { "date": "2026-08-12", "doc": "TL-260812-101", "note": "Тўлов",          "debit": 0,       "credit": 5400000 }
  ]
}
```

| Kalit | Tip | Izoh |
|---|---|---|
| `supplier_id` | id | taminotchi kodi |
| `name` | string | taminotchi nomi — PDF sarlavhasida |
| `date_from` / `date_to` | date | so'ralgan davr (aks-sado; berilmasa bot so'rovdagi sanalarni ishlatadi) |
| `opening_balance` | money | davr boshidagi qoldiq (taminotchi foydasiga musbat) |
| `total_debit` | money | davr ichida yuk berilishi (kompaniya qarzi oshishi) — `rows[].debit` yig'indisi |
| `total_credit` | money | to'lovlar va qaytarishlar — `rows[].credit` yig'indisi |
| `closing_balance` | money | `opening_balance + total_debit − total_credit` |
| `rows[]` | list | harakatlar, `date` bo'yicha **o'sish** tartibida |
| `rows[].date` | date | harakat sanasi |
| `rows[].doc` | string | hujjat raqami |
| `rows[].note` | string | izoh. 1C hozir `Номи--нарх(ВАЛЮТА) Х миқдор` shaklida yuboradi (masalan `Авалон Кондиционер 32--450(USD) Х 100`) — bot uni **ajratib**, hujjatda nomi, narxi va miqdorini alohida ustunlarda ko'rsatadi. Boshqa shaklda kelsa matn o'zgarishsiz chiqadi |
| `rows[].debit` | money | debet (yuk berilishi); bo'lmasa `0` |
| `rows[].credit` | money | kredit (to'lov/qaytarish); bo'lmasa `0` |

> Tekshiruvlar: `closing_balance = opening_balance + total_debit − total_credit`; joriy sanagacha akt `closing_balance` (shu valyutada) = `getBalance` dagi o'sha valyuta qatori; har bir qatorda `debit` va `credit` dan faqat bittasi > 0.

---

## 10. Endpoint ↔ bot mosligi

| 1C endpoint | Prefiks | `SupplierService` metodi | Bot bo'limi |
|---|---|---|---|
| `checkNumber` ✅ | `client_bot` | `check_number` → `APIService.register_device` | Рўйхатдан ўтиш (telefon) — MX-Client-Bot bilan bir xil |
| `getClientInfo` ✅ | `client_bot` | `get_cabinet` | 👤 Кабинет (profil) |
| `getBalance` | `supplier_bot` | `get_balance` | 💳 Баланс, 👤 Кабинет |
| `getPaymentsSupplier` | `supplier_bot` | `get_payments` / `get_payment` | 💰 Тўловлар (ro'yxat, detallar, holat) |
| `confirmPayment` | `supplier_bot` | `confirm_payment` | 💰 Тўловни тасдиқлаш |
| `getShipments` | `supplier_bot` | `get_shipments` / `get_shipment` | 📦 Берилган юклар |
| `getReturns` | `supplier_bot` | `get_returns` / `get_return` | 🔄 Юк қайтариш (ro'yxat, detallar, holat) |
| `confirmReturn` | `supplier_bot` | `confirm_return` | 🔄 Қайтаришни тасдиқлаш |
| `getBonuses` | `supplier_bot` | `get_bonuses` | 🎁 Бонуслар, 👤 Кабинет |
| `getcry` | `supplier_bot` | `get_currencies` | 📄 Акт сверка — валюта танлаш |
| `getAktSverka` | `supplier_bot` | `get_akt_sverka` | 📄 Акт сверка (+ PDF), танланган валютада |

> **Bitta hujjat uchun alohida endpoint kerak emas:** hujjat tafsiloti (to'lov / yuk / qaytarish detallari) ro'yxat javobidan olinadi — bot `getPaymentsSupplier` / `getShipments` / `getReturns` natijasidan kerakli `*_id` ni o'zi filtrlaydi. Shuning uchun ro'yxat javobida **barcha** maydonlar (jumladan `products[]`) to'liq bo'lishi kerak.
>
> Har bir bo'lim bot va WebApp da bir xil ishlaydi — ikkalasi ham shu endpointlarga murojaat qiladi, farqi faqat tasdiqlashdagi `source` maydonida.

---

## 11. 1C jamoasi uchun tekshiruv ro'yxati

- [ ] Yangi endpointlar `hs/supplier_bot/api/` ostida, Basic Auth bilan; noto'g'ri parol → `401`
- [ ] Javoblar UTF-8 JSON; sanalar `YYYY-MM-DD`; summalar **son** (string emas); bo'sh ro'yxat `[]`
- [ ] Har bir so'rovda `supplier_id` tekshiriladi: boshqa taminotchi hujjati so'ralsa → `404` (TZ 4)
- [ ] `checkNumber` tanasidagi `botID` qabul qilinadi va taminotchi kartochkasiga saqlanadi (1-bo'lim)
- [ ] To'lovlarda `status` **doim aniq** yuboriladi — bo'lmasa bot uni `confirmed` deb qabul qiladi va tasdiqlash tugmasi chiqmaydi (0.5-bo'lim)
- [ ] `confirmPayment` / `confirmReturn` idempotent va 1C da qayd etiladi: kim (`chat_id`), qachon, qaysi kanaldan (`source`: `telegram_bot` / `webapp`) (TZ 3)
- [ ] `getBalance.balance` = joriy sanagacha `getAktSverka.closing_balance`
- [ ] `getAktSverka`: `closing = opening + total_debit − total_credit`; `rows` sana bo'yicha o'sish tartibida; qatorda `debit`/`credit` dan faqat bittasi > 0
- [ ] `getBonuses`: `remaining = accrued − used`
- [ ] `getcry` valyutalar ro'yxatini qaytaradi; `getAktSverka` `cry_id` ni hisobga oladi va summalarni shu valyutada beradi
- [ ] `getShipments` / `getReturns` da har bir tovar uchun `name`, `qty`, `price`, `sum` to'ldirilgan (TZ 2.2)
- [ ] Enum qiymatlar aynan hujjatdagidek (kichik harf, lotin)
- [ ] Test: `/panel/api-logs` da har bir endpoint javobi kutilgan shakl bilan solishtiriladi

Savollar: bot/backend jamoasi — `torex.amaki@gmail.com`.
