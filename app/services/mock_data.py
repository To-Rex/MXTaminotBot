"""Mock (namunaviy) ma'lumotlar — 1C endpointlari hali tayyor bo'lmaganda ishlatiladi.

TZ bo'yicha 1C da hali mavjud bo'lmagan supplier endpointlari uchun bot demo
rejimda shu ma'lumotlar bilan ishlaydi (docs/1C_SUPPLIER_API.md, SUPPLIER_MOCK_FALLBACK).

Ma'lumotlar supplier_id dan deterministik seed bilan yaratiladi — bir xil
taminotchi har doim bir xil ro'yxatni ko'radi. Tasdiqlash (confirmPayment /
confirmReturn) holati jarayon xotirasida saqlanadi, shuning uchun tasdiqlangan
hujjat qayta so'ralganda "confirmed" bo'lib qoladi.

Saqlanadigan yozuvlarda matn emas, **kodlar** turadi (tovar indeksi, ombor
indeksi, sabab indeksi); inson o'qiydigan matn o'qish paytida ``lang`` bo'yicha
qo'yiladi — shuning uchun demo ma'lumot ham interfeys tili bilan birga almashadi.
"""
import hashlib
import random
from datetime import date, datetime, timedelta
from typing import Any, Optional

# supplier_id -> generated dataset (payments, shipments, returns, bonuses…)
_store: dict[str, dict[str, Any]] = {}

# (uz, ru, narx)
_PRODUCTS = [
    ("Ун олий нав 50 кг", "Мука высший сорт 50 кг", 285000),
    ("Шакар 50 кг қоп", "Сахар 50 кг мешок", 640000),
    ("Ўсимлик ёғи 5 л", "Масло растительное 5 л", 92000),
    ("Гуруч лазер 25 кг", "Рис лазер 25 кг", 425000),
    ("Макарон 400 г (блок 20)", "Макароны 400 г (блок 20)", 168000),
    ("Чой қора 250 г (блок 40)", "Чай чёрный 250 г (блок 40)", 520000),
    ("Сув 1.5 л (блок 6)", "Вода 1.5 л (блок 6)", 21000),
    ("Консерва мол гўшти (блок 24)", "Тушёнка говяжья (блок 24)", 696000),
]

_WAREHOUSES = [("Марказий омбор", "Центральный склад"), ("2-омбор", "Склад №2")]

_RETURN_REASONS = [
    ("Яроқлилик муддати яқин", "Истекает срок годности", "expiry"),
    ("Қадоқ шикастланган", "Повреждена упаковка", "damaged"),
    ("Ортиқча етказилган", "Излишняя поставка", "excess"),
]

_TXT = {
    "payment_note": ("Юк {doc} учун тўлов", "Оплата за отгрузку {doc}"),
    "bonus_accrued": ("Юк {doc} бўйича 2% бонус", "2% бонус по отгрузке {doc}"),
    "bonus_used": ("Ўзаро ҳисоб-китобда фойдаланилди", "Использовано во взаиморасчётах"),
    "akt_shipment": ("Юк берилди", "Отгрузка"),
    "akt_payment": ("Тўлов", "Оплата"),
    "akt_return": ("Юк қайтарилди", "Возврат"),
}


def _i(lang: str) -> int:
    """Til indeksi: uz → 0, ru → 1."""
    return 1 if str(lang).lower().startswith("ru") else 0


def _txt(key: str, lang: str, **kw) -> str:
    s = _TXT[key][_i(lang)]
    return s.format(**kw) if kw else s


def _seed(supplier_id: str) -> int:
    return int(hashlib.sha256(str(supplier_id).encode()).hexdigest()[:8], 16)


def supplier_name(phone: str) -> str:
    tail = str(phone)[-4:] if phone else "0000"
    return f"МЧЖ «Таъминот-{tail}»"


def check_number(phone: str) -> dict:
    """Mock checkNumber: telefon raqamidan barqaror supplier id yasaydi."""
    sid = 70000 + _seed(phone) % 9000
    name = supplier_name(phone)
    # keyingi so'rovlar (kabinet, akt sverka) ham shu nomni ko'rsatsin
    _dataset(str(sid))["name"] = name
    return {"id": sid, "name": name}


def _dataset(supplier_id: str) -> dict[str, Any]:
    if supplier_id in _store:
        return _store[supplier_id]

    rng = random.Random(_seed(supplier_id))
    today = date.today()
    sid_num = int(str(supplier_id)) if str(supplier_id).isdigit() else _seed(supplier_id) % 90000

    # ── berilgan yuklar (shipments) ─────────────────────────────────────
    shipments = []
    for i in range(6):
        d = today - timedelta(days=8 + i * 16 + rng.randint(0, 5))
        products = []
        for pidx in rng.sample(range(len(_PRODUCTS)), rng.randint(2, 4)):
            price = _PRODUCTS[pidx][2]
            qty = rng.randint(5, 60)
            products.append({"pidx": pidx, "product_id": 1000 + pidx,
                             "qty": qty, "price": price, "sum": price * qty})
        shipments.append({
            "shipment_id": 41000 + i,
            "doc_number": f"YT-{d:%y%m}-{sid_num % 1000:03d}{i}",
            "date": d.isoformat(),
            "wh": i % 2,
            "total": sum(p["sum"] for p in products),
            "products": products,
        })
    shipments.sort(key=lambda s: s["date"], reverse=True)

    # ── to'lovlar (payments) ────────────────────────────────────────────
    methods = ["transfer", "cash", "transfer", "card", "transfer", "cash"]
    payments = []
    for i, sh in enumerate(shipments):
        d = date.fromisoformat(sh["date"]) + timedelta(days=rng.randint(4, 12))
        if d > today:
            d = today - timedelta(days=rng.randint(0, 3))
        status = "pending" if i < 2 else "confirmed"
        payments.append({
            "payment_id": 90100 + i,
            "doc_number": f"TL-{d:%y%m%d}-{100 + i}",
            "date": d.isoformat(),
            "amount": round(sh["total"] * rng.choice((0.5, 0.7, 1.0))),
            "method": methods[i % len(methods)],
            "status": status,
            "confirmed_at": (d + timedelta(days=1)).isoformat() if status == "confirmed" else None,
            "for_doc": sh["doc_number"],
        })
    payments.sort(key=lambda p: p["date"], reverse=True)

    # ── qaytarishlar (returns) ──────────────────────────────────────────
    returns = []
    for i in range(3):
        src = shipments[i * 2 if i * 2 < len(shipments) else 0]
        d = date.fromisoformat(src["date"]) + timedelta(days=rng.randint(2, 9))
        picked = rng.choice(src["products"])
        qty = max(1, picked["qty"] // rng.randint(3, 6))
        returns.append({
            "return_id": 55200 + i,
            "doc_number": f"QT-{d:%y%m}-{200 + i}",
            "date": d.isoformat(),
            "status": "pending" if i == 0 else "confirmed",
            "confirmed_at": (d + timedelta(days=1)).isoformat() if i != 0 else None,
            "reason_idx": rng.randrange(len(_RETURN_REASONS)),
            "total": picked["price"] * qty,
            "products": [{**picked, "qty": qty, "sum": picked["price"] * qty}],
        })
    returns.sort(key=lambda r: r["date"], reverse=True)

    # ── bonuslar ────────────────────────────────────────────────────────
    bonus_items = []
    accrued_total = 0
    for i, sh in enumerate(shipments[:5]):
        amount = round(sh["total"] * 0.02)
        accrued_total += amount
        bonus_items.append({"bonus_id": 7700 + i, "date": sh["date"], "kind": "accrued",
                            "amount": amount, "for_doc": sh["doc_number"]})
    used = round(accrued_total * 0.4)
    bonus_items.append({"bonus_id": 7790, "date": (today - timedelta(days=12)).isoformat(),
                        "kind": "used", "amount": used, "for_doc": ""})
    bonus_items.sort(key=lambda b: b["date"], reverse=True)

    data = {
        "supplier_id": supplier_id,
        "name": supplier_name(str(supplier_id)),
        "shipments": shipments,
        "payments": payments,
        "returns": returns,
        "bonuses": {"accrued": accrued_total, "used": used,
                    "remaining": accrued_total - used, "items": bonus_items},
        "balance": 0,
    }
    _store[supplier_id] = data
    _recalc_balance(data)
    return data


def _recalc_balance(data: dict) -> None:
    shipped = sum(s["total"] for s in data["shipments"])
    paid = sum(p["amount"] for p in data["payments"] if p["status"] == "confirmed")
    returned = sum(r["total"] for r in data["returns"] if r["status"] == "confirmed")
    data["balance"] = shipped - paid - returned


# ── til bo'yicha matn qo'yish ───────────────────────────────────────────────
def _products_out(products: list[dict], lang: str) -> list[dict]:
    li = _i(lang)
    return [{"product_id": p["product_id"], "name": _PRODUCTS[p["pidx"]][li],
             "qty": p["qty"], "price": p["price"], "sum": p["sum"]} for p in products]


# ── public mock API (1C javob shakllari bilan bir xil) ──────────────────────
def get_cabinet(supplier_id: str, phone: str = "", lang: str = "uz") -> dict:
    """getClientInfo mock — kabinet profili."""
    d = _dataset(supplier_id)
    rng = random.Random(_seed(supplier_id))
    registered = date.today() - timedelta(days=200 + rng.randint(0, 400))
    return {
        "client_id": supplier_id,
        "name": d["name"],
        "phone": phone or f"9989{_seed(supplier_id) % 100000000:08d}",
        "status": "Фаол таъминотчи" if _i(lang) == 0 else "Активный поставщик",
        "registered_at": registered.isoformat(),
    }


def get_balance(supplier_id: str) -> dict:
    d = _dataset(supplier_id)
    return {"balance": d["balance"], "currency": "UZS",
            "as_of": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}


def get_payments(supplier_id: str, lang: str = "uz") -> list[dict]:
    out = []
    for p in _dataset(supplier_id)["payments"]:
        out.append({k: v for k, v in p.items() if k != "for_doc"})
        out[-1]["note"] = _txt("payment_note", lang, doc=p["for_doc"]) if p["for_doc"] else ""
    return out


def confirm_payment(supplier_id: str, payment_id: int) -> Optional[dict]:
    d = _dataset(supplier_id)
    for p in d["payments"]:
        if p["payment_id"] == int(payment_id):
            if p["status"] != "confirmed":
                p["status"] = "confirmed"
                p["confirmed_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                _recalc_balance(d)
            return {"success": True, "payment_id": p["payment_id"],
                    "status": p["status"], "confirmed_at": p["confirmed_at"]}
    return None


def get_shipments(supplier_id: str, lang: str = "uz") -> list[dict]:
    li = _i(lang)
    return [{
        "shipment_id": s["shipment_id"], "doc_number": s["doc_number"], "date": s["date"],
        "warehouse": _WAREHOUSES[s["wh"]][li], "total": s["total"],
        "products": _products_out(s["products"], lang),
    } for s in _dataset(supplier_id)["shipments"]]


def get_returns(supplier_id: str, lang: str = "uz") -> list[dict]:
    li = _i(lang)
    out = []
    for r in _dataset(supplier_id)["returns"]:
        reason = _RETURN_REASONS[r["reason_idx"]]
        out.append({
            "return_id": r["return_id"], "doc_number": r["doc_number"], "date": r["date"],
            "status": r["status"], "confirmed_at": r["confirmed_at"],
            "reason": reason[li], "reason_code": reason[2], "total": r["total"],
            "products": _products_out(r["products"], lang),
        })
    return out


def confirm_return(supplier_id: str, return_id: int) -> Optional[dict]:
    d = _dataset(supplier_id)
    for r in d["returns"]:
        if r["return_id"] == int(return_id):
            if r["status"] != "confirmed":
                r["status"] = "confirmed"
                r["confirmed_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                _recalc_balance(d)
            return {"success": True, "return_id": r["return_id"],
                    "status": r["status"], "confirmed_at": r["confirmed_at"]}
    return None


def get_bonuses(supplier_id: str, lang: str = "uz") -> dict:
    b = _dataset(supplier_id)["bonuses"]
    items = []
    for i in b["items"]:
        note = (_txt("bonus_accrued", lang, doc=i["for_doc"]) if i["kind"] == "accrued"
                else _txt("bonus_used", lang))
        items.append({"bonus_id": i["bonus_id"], "date": i["date"], "kind": i["kind"],
                      "amount": i["amount"], "note": note})
    return {"accrued": b["accrued"], "used": b["used"], "remaining": b["remaining"], "items": items}


def get_akt_sverka(supplier_id: str, date_from: date, date_to: date, lang: str = "uz") -> dict:
    """Akt sverka: yuk berish → debet (kompaniya qarzi oshadi), to'lov/qaytarish → kredit."""
    d = _dataset(supplier_id)
    rows = []
    for s in d["shipments"]:
        rows.append({"date": s["date"], "doc": s["doc_number"],
                     "note": _txt("akt_shipment", lang), "debit": s["total"], "credit": 0})
    for p in d["payments"]:
        if p["status"] == "confirmed":
            rows.append({"date": p["date"], "doc": p["doc_number"],
                         "note": _txt("akt_payment", lang), "debit": 0, "credit": p["amount"]})
    for r in d["returns"]:
        if r["status"] == "confirmed":
            rows.append({"date": r["date"], "doc": r["doc_number"],
                         "note": _txt("akt_return", lang), "debit": 0, "credit": r["total"]})
    rows.sort(key=lambda r: r["date"])

    opening = 0
    period_rows = []
    for r in rows:
        rd = date.fromisoformat(r["date"])
        if rd < date_from:
            opening += r["debit"] - r["credit"]
        elif rd <= date_to:
            period_rows.append(r)
    total_debit = sum(r["debit"] for r in period_rows)
    total_credit = sum(r["credit"] for r in period_rows)
    return {
        "supplier_id": supplier_id,
        "name": d["name"],
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "opening_balance": opening,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "closing_balance": opening + total_debit - total_credit,
        "rows": period_rows,
    }
