"""Таъминотчи (supplier) data service — 1C HTTP-servis klienti + mock fallback.

Endpointlar va javob shakllari: docs/1C_SUPPLIER_API.md
Base: {base_url}/hs/supplier_bot/api/<endpoint>, HTTP Basic Auth (har bir bot uchun panel sozlamasi).

Failure model:
  * ``ServiceUnavailable`` — endpoint hali yozilmagan / 1C ishlamayapti / javob shakli noto'g'ri.
  * ``SupplierError``      — 1C biznes xato qaytardi ({"error": {code, message}}).

TZ bo'yicha 1C da hali tayyor bo'lmagan endpointlar uchun mock fallback bor:
``SUPPLIER_MOCK_FALLBACK=1`` (default) bo'lsa, ServiceUnavailable holatida
``app.services.mock_data`` dagi namunaviy ma'lumot qaytadi va natijaga
``_mock: True`` belgisi qo'shiladi — bot foydalanuvchiga demo rejim haqida
ogohlantirish ko'rsatadi. 1C endpoint tayyor bo'lgach hech narsa o'zgartirmasdan
real ma'lumot ishlaydi.
"""
import base64
import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx

from app.config import SUPPLIER_MOCK_FALLBACK
from app.services import api_log, mock_data
from app.services.api import APIService
from app.services.http_client import get_http_client

logger = logging.getLogger(__name__)

API_PREFIX = "/hs/supplier_bot/api/"
# MX-Client-Bot bilan umumiy (1C da allaqachon tayyor) endpointlar shu prefiksda:
# checkNumber (login), getClientInfo (kabinet).
CLIENT_API_PREFIX = "/hs/client_bot/api/"
TIMEOUT = 30.0

# Expected response skeletons per endpoint (docs/1C_SUPPLIER_API.md) — shown in
# /panel/api-logs next to the actual answer so the 1C team sees the difference.
EXPECTED_SHAPES: dict[str, Any] = {
    "checkNumber": {"id": 70123, "name": 'MCHJ "Taminot Trade"'},
    "getClientInfo": {
        "client_id": 70123, "name": 'MCHJ "Taminot Trade"', "phone": "998901234567",
        "status": "Фаол таъминотчи", "registered_at": "2025-03-14",
    },
    "getSupplierInfo": {
        "supplier_id": 70123, "name": 'MCHJ "Taminot Trade"',
        "balance": 12500000, "as_of": "2026-08-20T12:00:00",
    },
    "getBalance": {"balance": 12500000, "currency": "UZS", "as_of": "2026-08-20T12:00:00"},
    "getPayments": [{
        "payment_id": 90101, "doc_number": "TL-260812-101", "date": "2026-08-12",
        "amount": 5400000, "method": "transfer", "status": "pending",
        "confirmed_at": None, "note": "Юк YT-2608-0011 учун тўлов",
    }],
    "confirmPayment": {"success": True, "payment_id": 90101, "status": "confirmed",
                       "confirmed_at": "2026-08-20T12:05:00"},
    "getShipments": [{
        "shipment_id": 41001, "doc_number": "YT-2608-0011", "date": "2026-08-10",
        "warehouse": "Марказий омбор", "total": 8200000,
        "products": [{"product_id": 1001, "name": "Un oliy nav 50kg", "qty": 20,
                      "price": 285000, "sum": 5700000}],
    }],
    "getReturns": [{
        "return_id": 55201, "doc_number": "QT-2608-201", "date": "2026-08-14",
        "status": "pending", "confirmed_at": None,
        "reason": "Qadoq shikastlangan", "reason_code": "damaged", "total": 570000,
        "products": [{"product_id": 1001, "name": "Un oliy nav 50kg", "qty": 2,
                      "price": 285000, "sum": 570000}],
    }],
    "confirmReturn": {"success": True, "return_id": 55201, "status": "confirmed",
                      "confirmed_at": "2026-08-20T12:05:00"},
    "getBonuses": {
        "accrued": 820000, "used": 300000, "remaining": 520000,
        "items": [{"bonus_id": 7701, "date": "2026-08-10", "kind": "accrued",
                   "amount": 164000, "note": "Юк YT-2608-0011 бўйича 2% бонус"}],
    },
    "getAktSverka": {
        "supplier_id": 70123, "name": 'MCHJ "Taminot Trade"',
        "date_from": "2026-08-01", "date_to": "2026-08-31",
        "opening_balance": 4300000, "total_debit": 8200000, "total_credit": 5400000,
        "closing_balance": 7100000,
        "rows": [{"date": "2026-08-10", "doc": "YT-2608-0011", "note": "Юк берилди",
                  "debit": 8200000, "credit": 0}],
    },
}


class ServiceUnavailable(Exception):
    """1C endpoint is not ready (404/5xx/empty or malformed body/network error)."""

    def __init__(self, endpoint: str, reason: str = ""):
        self.endpoint = endpoint
        self.reason = reason
        super().__init__(f"{endpoint} unavailable: {reason}")


class SupplierError(Exception):
    """1C returned a business error: {"error": {"code": ..., "message": ...}}."""

    def __init__(self, code: str, message: str, endpoint: str = ""):
        self.code = code
        self.message = message
        self.endpoint = endpoint
        super().__init__(f"{endpoint} {code}: {message}")


# ────────────────────────────────────────────────────────────────────────────
# low-level helpers
# ────────────────────────────────────────────────────────────────────────────
def _today() -> date:
    return datetime.now(timezone.utc).date()


def _fmt_money(v: float) -> str:
    return f"{float(v or 0):,.0f}".replace(",", " ") + " сўм"


def _fmt_date(d: Any) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%d.%m.%Y")
    p = _parse_date(d)
    return p.strftime("%d.%m.%Y") if p else (str(d) if d else "")


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _parse_date(v: Any) -> Optional[date]:
    """Accepts 'YYYY-MM-DD', ISO datetime, 'DD.MM.YYYY', 'YYYYMMDD'."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%d.%m.%Y", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(s[:n], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def _parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).strip())
    except ValueError:
        d = _parse_date(v)
        return datetime(d.year, d.month, d.day) if d else None


def _raise_if_error(endpoint: str, data: Any) -> None:
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        err = data["error"]
        raise SupplierError(str(err.get("code") or "ERROR"), str(err.get("message") or "Хатолик"), endpoint)


async def _request(
    method: str, base_url: str, login: str, password: str, endpoint: str,
    params: Optional[dict] = None, body: Optional[dict] = None,
    prefix: str = API_PREFIX,
) -> Any:
    """Perform request; return parsed JSON. Raises ServiceUnavailable / SupplierError.

    GET so'rovlarda vaqtinchalik xatolar (tarmoq, 5xx, bo'sh tana) bir marta
    qayta uriniladi. POST lar takrorlanmaydi (idempotent emas).
    Every call (success or failure) is recorded to ``api_log``.
    """
    attempts = 2 if method == "GET" else 1
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        if attempt:
            import asyncio
            await asyncio.sleep(0.7)
        try:
            return await _request_once(method, base_url, login, password, endpoint, params, body, prefix)
        except ServiceUnavailable as e:
            last_exc = e
            if e.reason == "empty body" and attempt == 0:
                continue
            if e.reason.startswith(("network", "http 5")) and attempt == 0:
                continue
            raise
    raise last_exc  # type: ignore[misc]


async def _request_once(
    method: str, base_url: str, login: str, password: str, endpoint: str,
    params: Optional[dict] = None, body: Optional[dict] = None,
    prefix: str = API_PREFIX,
) -> Any:
    if not base_url:
        raise ServiceUnavailable(endpoint, "base_url is empty (bot settings)")
    url = f"{base_url.rstrip('/')}{prefix}{endpoint}"
    creds = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    logger.info("📡 1C %s %s params=%s body=%s", method, url, params, body)
    started = time.monotonic()

    def _log(status=None, response=None, outcome="ok", error=""):
        api_log.record(
            endpoint=endpoint, method=method, url=url, params=params, request_body=body,
            status_code=status, response_body=response, outcome=outcome, error=error,
            duration_ms=(time.monotonic() - started) * 1000,
            expected=EXPECTED_SHAPES.get(endpoint) if outcome != "ok" else None,
        )

    try:
        resp = await get_http_client().request(method, url, params=params, json=body, headers=headers, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        logger.error("❌ 1C %s network error: %s", endpoint, e)
        _log(outcome="unavailable", error=f"network: {e}")
        raise ServiceUnavailable(endpoint, f"network: {e}")
    text = resp.text or ""
    logger.info("📡 1C %s RESPONSE %s: %s", endpoint, resp.status_code, text[:400])

    if resp.status_code == 401:
        _log(resp.status_code, text, "error", "UNAUTHORIZED")
        raise SupplierError("UNAUTHORIZED", "1C логин/парол нотўғри (панелдан текширинг)", endpoint)
    if resp.status_code in (404, 405, 501) or resp.status_code >= 500:
        _log(resp.status_code, text, "unavailable", f"http {resp.status_code}")
        raise ServiceUnavailable(endpoint, f"http {resp.status_code}")
    if not text.strip():
        _log(resp.status_code, "", "unavailable",
             "bo'sh tana keldi — hujjatdagi JSON kutilgan edi (endpoint hali yozilmagan bo'lsa kerak)")
        raise ServiceUnavailable(endpoint, "empty body")
    try:
        data = resp.json()
    except ValueError:
        _log(resp.status_code, text, "unavailable",
             f"JSON emas — kelgani: {text.strip()[:60]!r}…; hujjatdagi JSON kutilgan edi")
        raise ServiceUnavailable(endpoint, "non-JSON body")
    try:
        _raise_if_error(endpoint, data)
    except SupplierError as e:
        _log(resp.status_code, data, "error", f"{e.code}: {e.message}")
        raise
    if resp.status_code >= 400:
        _log(resp.status_code, data, "error", f"http {resp.status_code}")
        raise SupplierError(f"HTTP_{resp.status_code}", str(data)[:200], endpoint)
    _log(resp.status_code, data, "ok")
    return data


async def _get(base_url, login, password, endpoint, **params) -> Any:
    return await _request("GET", base_url, login, password, endpoint,
                          params={k: v for k, v in params.items() if v is not None})


async def _post(base_url, login, password, endpoint, body: dict) -> Any:
    return await _request("POST", base_url, login, password, endpoint, body=body)


def _expect_list(endpoint: str, data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "data", "list", "rows", endpoint):
            if isinstance(data.get(key), list):
                return data[key]
    raise ServiceUnavailable(endpoint, "unexpected shape (not a list)")


def _expect_dict(endpoint: str, data: Any, must_have: tuple = ()) -> dict:
    # 1C tolerance: a single object sometimes arrives wrapped in a one-element list.
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise ServiceUnavailable(endpoint, "unexpected shape (not an object)")
    if must_have and not any(k in data for k in must_have):
        raise ServiceUnavailable(endpoint, f"unexpected shape (missing {must_have})")
    return data


# ────────────────────────────────────────────────────────────────────────────
# mappers: 1C JSON → bot dicts
# ────────────────────────────────────────────────────────────────────────────
PAYMENT_STATUSES = ("pending", "confirmed", "cancelled")
RETURN_STATUSES = ("pending", "confirmed")


def _map_product(p: dict) -> dict:
    qty = _to_float(p.get("qty"), 1.0)
    price = _to_float(p.get("price"))
    return {
        "product_id": _to_int(p.get("product_id")),
        "name": str(p.get("name") or "Товар"),
        "qty": int(qty) if qty == int(qty) else qty,
        "price": price,
        "sum": _to_float(p.get("sum"), price * qty),
    }


def _map_payment(p: dict) -> dict:
    status = str(p.get("status") or "confirmed").lower()
    return {
        "payment_id": _to_int(p.get("payment_id")),
        "doc_number": str(p.get("doc_number") or p.get("payment_id") or ""),
        "date": _parse_date(p.get("date")) or _today(),
        "amount": _to_float(p.get("amount")),
        "method": str(p.get("method") or "other").lower(),
        "status": status if status in PAYMENT_STATUSES else "confirmed",
        "confirmed_at": _parse_dt(p.get("confirmed_at")),
        "note": str(p.get("note") or ""),
    }


def _map_shipment(s: dict) -> dict:
    products = [_map_product(p) for p in (s.get("products") or []) if isinstance(p, dict)]
    return {
        "shipment_id": _to_int(s.get("shipment_id")),
        "doc_number": str(s.get("doc_number") or s.get("shipment_id") or ""),
        "date": _parse_date(s.get("date")) or _today(),
        "warehouse": str(s.get("warehouse") or ""),
        "products": products,
        "total": _to_float(s.get("total"), sum(p["sum"] for p in products)),
    }


def _map_return(r: dict) -> dict:
    products = [_map_product(p) for p in (r.get("products") or []) if isinstance(p, dict)]
    status = str(r.get("status") or "pending").lower()
    return {
        "return_id": _to_int(r.get("return_id")),
        "doc_number": str(r.get("doc_number") or r.get("return_id") or ""),
        "date": _parse_date(r.get("date")) or _today(),
        "status": status if status in RETURN_STATUSES else "pending",
        "confirmed_at": _parse_dt(r.get("confirmed_at")),
        "reason": str(r.get("reason") or ""),
        "reason_code": str(r.get("reason_code") or ""),
        "products": products,
        "total": _to_float(r.get("total"), sum(p["sum"] for p in products)),
    }


def _map_bonus_item(b: dict) -> dict:
    kind = str(b.get("kind") or "accrued").lower()
    return {
        "bonus_id": _to_int(b.get("bonus_id")),
        "date": _parse_date(b.get("date")) or _today(),
        "kind": kind if kind in ("accrued", "used") else "accrued",
        "amount": _to_float(b.get("amount")),
        "note": str(b.get("note") or ""),
    }


def _map_akt_row(r: dict) -> dict:
    return {
        "date": _parse_date(r.get("date")) or _today(),
        "doc": str(r.get("doc") or ""),
        "note": str(r.get("note") or ""),
        "debit": _to_float(r.get("debit")),
        "credit": _to_float(r.get("credit")),
    }


def _map_akt(d: dict, supplier_id: str, date_from: date, date_to: date) -> dict:
    rows = [_map_akt_row(r) for r in (d.get("rows") or []) if isinstance(r, dict)]
    rows.sort(key=lambda r: r["date"])
    return {
        "supplier_id": str(d.get("supplier_id") or supplier_id),
        "name": str(d.get("name") or ""),
        "date_from": _parse_date(d.get("date_from")) or date_from,
        "date_to": _parse_date(d.get("date_to")) or date_to,
        "opening_balance": _to_float(d.get("opening_balance")),
        "total_debit": _to_float(d.get("total_debit"), sum(r["debit"] for r in rows)),
        "total_credit": _to_float(d.get("total_credit"), sum(r["credit"] for r in rows)),
        "closing_balance": _to_float(d.get("closing_balance")),
        "rows": rows,
    }


def _mock_used(endpoint: str, e: ServiceUnavailable) -> None:
    logger.info("🧪 mock fallback for %s (1C: %s)", endpoint, e.reason)


# ────────────────────────────────────────────────────────────────────────────
# service facade
# ────────────────────────────────────────────────────────────────────────────
class SupplierService:
    fmt_money = staticmethod(_fmt_money)
    fmt_date = staticmethod(_fmt_date)

    # ── 1. registration — login MX-Client-Bot bilan BIR XIL ────────────
    @staticmethod
    async def check_number(base_url: str, login: str, password: str,
                           phone: str, chat_id: str, bot_id: int = 0) -> Optional[dict]:
        """Telefon raqami bo'yicha taminotchini topadi va chat_id ni bog'laydi.

        MX-Client-Bot dagi login bilan aynan bir xil: ``APIService.register_device``
        (POST /hs/client_bot/api/checkNumber, topilmasa legacy /hs/client/api/device).
        So'rov tanasi: ``{"phoneNumber": <int>, "chatID": "<str>", "botID": <int>}`` —
        ``botID`` panel'dagi bot raqami, 1C uni taminotchi kartochkasiga saqlaydi.
        Returns {"id": ..., "name": ...} yoki None (topilmadi).

        1C umuman javob bermasa (tarmoq/xizmat yo'q → result None) va mock rejim
        yoqilgan bo'lsa — demo taminotchi qaytadi. 1C "topilmadi" deb javob bersa
        (id siz JSON) — MX-Client-Bot dagidek None qaytadi.
        """
        result = await APIService.register_device(base_url, login, password, phone,
                                                  str(chat_id), int(bot_id or 0))
        if result and result.get("id"):
            return {"id": result["id"], "name": str(result.get("name") or "")}
        if result is None and SUPPLIER_MOCK_FALLBACK:
            logger.info("🧪 mock fallback for checkNumber (1C javob bermadi)")
            return {**mock_data.check_number(str(phone)), "_mock": True}
        return None

    # ── 2. cabinet — getClientInfo (1C da tayyor, MX-Client-Bot bilan umumiy) ─
    @staticmethod
    async def get_cabinet(base_url: str, login: str, password: str,
                          supplier_id: str, phone: str = "", lang: str = "uz") -> dict:
        """👤 Кабинет: GET /hs/client_bot/api/getClientInfo?client_id=<supplier_id>."""
        try:
            data = await _request("GET", base_url, login, password, "getClientInfo",
                                  params={"client_id": supplier_id}, prefix=CLIENT_API_PREFIX)
            d = _expect_dict("getClientInfo", data, ("client_id", "name"))
            mock = False
        except ServiceUnavailable as e:
            if not SUPPLIER_MOCK_FALLBACK:
                raise
            _mock_used("getClientInfo", e)
            d, mock = mock_data.get_cabinet(supplier_id, phone, lang), True
        reg = _parse_date(d.get("registered_at"))
        return {
            "supplier_id": str(d.get("client_id") or d.get("supplier_id") or supplier_id),
            "name": str(d.get("name") or "Таъминотчи"),
            "phone": str(d.get("phone") or phone or ""),
            "status": str(d.get("status") or "Таъминотчи"),
            "registered_at": _fmt_date(reg) if reg else str(d.get("registered_at") or "—"),
            "_mock": mock,
        }

    # ── info / balance (TZ 2.5) ─────────────────────────────────────────
    @staticmethod
    async def get_balance(base_url: str, login: str, password: str, supplier_id: str) -> dict:
        try:
            data = await _get(base_url, login, password, "getBalance", supplier_id=supplier_id)
            d = _expect_dict("getBalance", data, ("balance",))
        except ServiceUnavailable as e:
            if not SUPPLIER_MOCK_FALLBACK:
                raise
            _mock_used("getBalance", e)
            d = {**mock_data.get_balance(supplier_id), "_mock": True}
        return {
            "balance": _to_float(d.get("balance")),
            "currency": str(d.get("currency") or "UZS"),
            "as_of": _parse_dt(d.get("as_of")) or datetime.now(),
            "_mock": bool(d.get("_mock")),
        }

    # ── payments (TZ 2.1) ───────────────────────────────────────────────
    @staticmethod
    async def get_payments(base_url: str, login: str, password: str, supplier_id: str,
                           lang: str = "uz") -> list[dict]:
        try:
            data = await _get(base_url, login, password, "getPayments", supplier_id=supplier_id)
            rows, mock = _expect_list("getPayments", data), False
        except ServiceUnavailable as e:
            if not SUPPLIER_MOCK_FALLBACK:
                raise
            _mock_used("getPayments", e)
            rows, mock = mock_data.get_payments(supplier_id, lang), True
        out = [{**_map_payment(p), "_mock": mock} for p in rows if isinstance(p, dict)]
        out.sort(key=lambda p: p["date"], reverse=True)
        return out

    @staticmethod
    async def get_payment(base_url: str, login: str, password: str,
                          supplier_id: str, payment_id: int, lang: str = "uz") -> Optional[dict]:
        for p in await SupplierService.get_payments(base_url, login, password, supplier_id, lang):
            if p["payment_id"] == int(payment_id):
                return p
        return None

    @staticmethod
    async def confirm_payment(base_url: str, login: str, password: str,
                              supplier_id: str, payment_id: int, chat_id: str = "",
                              source: str = "telegram_bot") -> Optional[dict]:
        """Tasdiqlash 1C ga uzatiladi (TZ 3: тасдиқлаш операциялари 1Cга узатилади)."""
        try:
            data = await _post(base_url, login, password, "confirmPayment", {
                "supplier_id": _to_int(supplier_id), "payment_id": int(payment_id),
                "chat_id": str(chat_id or ""), "source": source,
            })
            d = _expect_dict("confirmPayment", data, ("success", "status"))
        except ServiceUnavailable as e:
            if not SUPPLIER_MOCK_FALLBACK:
                raise
            _mock_used("confirmPayment", e)
            res = mock_data.confirm_payment(supplier_id, payment_id)
            if not res:
                return None
            d = {**res, "_mock": True}
        return {
            "success": bool(d.get("success", True)),
            "payment_id": _to_int(d.get("payment_id"), int(payment_id)),
            "status": str(d.get("status") or "confirmed"),
            "confirmed_at": _parse_dt(d.get("confirmed_at")) or datetime.now(),
            "_mock": bool(d.get("_mock")),
        }

    # ── shipments (TZ 2.2) ──────────────────────────────────────────────
    @staticmethod
    async def get_shipments(base_url: str, login: str, password: str, supplier_id: str,
                            lang: str = "uz") -> list[dict]:
        try:
            data = await _get(base_url, login, password, "getShipments", supplier_id=supplier_id)
            rows, mock = _expect_list("getShipments", data), False
        except ServiceUnavailable as e:
            if not SUPPLIER_MOCK_FALLBACK:
                raise
            _mock_used("getShipments", e)
            rows, mock = mock_data.get_shipments(supplier_id, lang), True
        out = [{**_map_shipment(s), "_mock": mock} for s in rows if isinstance(s, dict)]
        out.sort(key=lambda s: s["date"], reverse=True)
        return out

    @staticmethod
    async def get_shipment(base_url: str, login: str, password: str,
                           supplier_id: str, shipment_id: int, lang: str = "uz") -> Optional[dict]:
        for s in await SupplierService.get_shipments(base_url, login, password, supplier_id, lang):
            if s["shipment_id"] == int(shipment_id):
                return s
        return None

    # ── returns (TZ 2.3) ────────────────────────────────────────────────
    @staticmethod
    async def get_returns(base_url: str, login: str, password: str, supplier_id: str,
                          lang: str = "uz") -> list[dict]:
        try:
            data = await _get(base_url, login, password, "getReturns", supplier_id=supplier_id)
            rows, mock = _expect_list("getReturns", data), False
        except ServiceUnavailable as e:
            if not SUPPLIER_MOCK_FALLBACK:
                raise
            _mock_used("getReturns", e)
            rows, mock = mock_data.get_returns(supplier_id, lang), True
        out = [{**_map_return(r), "_mock": mock} for r in rows if isinstance(r, dict)]
        out.sort(key=lambda r: r["date"], reverse=True)
        return out

    @staticmethod
    async def get_return(base_url: str, login: str, password: str,
                         supplier_id: str, return_id: int, lang: str = "uz") -> Optional[dict]:
        for r in await SupplierService.get_returns(base_url, login, password, supplier_id, lang):
            if r["return_id"] == int(return_id):
                return r
        return None

    @staticmethod
    async def confirm_return(base_url: str, login: str, password: str,
                             supplier_id: str, return_id: int, chat_id: str = "",
                             source: str = "telegram_bot") -> Optional[dict]:
        try:
            data = await _post(base_url, login, password, "confirmReturn", {
                "supplier_id": _to_int(supplier_id), "return_id": int(return_id),
                "chat_id": str(chat_id or ""), "source": source,
            })
            d = _expect_dict("confirmReturn", data, ("success", "status"))
        except ServiceUnavailable as e:
            if not SUPPLIER_MOCK_FALLBACK:
                raise
            _mock_used("confirmReturn", e)
            res = mock_data.confirm_return(supplier_id, return_id)
            if not res:
                return None
            d = {**res, "_mock": True}
        return {
            "success": bool(d.get("success", True)),
            "return_id": _to_int(d.get("return_id"), int(return_id)),
            "status": str(d.get("status") or "confirmed"),
            "confirmed_at": _parse_dt(d.get("confirmed_at")) or datetime.now(),
            "_mock": bool(d.get("_mock")),
        }

    # ── bonuses (TZ 2.4) ────────────────────────────────────────────────
    @staticmethod
    async def get_bonuses(base_url: str, login: str, password: str, supplier_id: str,
                          lang: str = "uz") -> dict:
        try:
            data = await _get(base_url, login, password, "getBonuses", supplier_id=supplier_id)
            d, mock = _expect_dict("getBonuses", data, ("accrued", "remaining", "items")), False
        except ServiceUnavailable as e:
            if not SUPPLIER_MOCK_FALLBACK:
                raise
            _mock_used("getBonuses", e)
            d, mock = mock_data.get_bonuses(supplier_id, lang), True
        items = [_map_bonus_item(b) for b in (d.get("items") or []) if isinstance(b, dict)]
        items.sort(key=lambda b: b["date"], reverse=True)
        accrued = _to_float(d.get("accrued"), sum(b["amount"] for b in items if b["kind"] == "accrued"))
        used = _to_float(d.get("used"), sum(b["amount"] for b in items if b["kind"] == "used"))
        return {
            "accrued": accrued,
            "used": used,
            "remaining": _to_float(d.get("remaining"), accrued - used),
            "items": items,
            "_mock": mock,
        }

    # ── akt sverka (TZ 2.6) ─────────────────────────────────────────────
    @staticmethod
    async def get_akt_sverka(base_url: str, login: str, password: str, supplier_id: str,
                             date_from: date, date_to: date, lang: str = "uz") -> dict:
        try:
            data = await _get(base_url, login, password, "getAktSverka",
                              supplier_id=supplier_id,
                              date_from=date_from.isoformat(), date_to=date_to.isoformat())
            d, mock = _expect_dict("getAktSverka", data, ("rows", "closing_balance")), False
        except ServiceUnavailable as e:
            if not SUPPLIER_MOCK_FALLBACK:
                raise
            _mock_used("getAktSverka", e)
            d, mock = mock_data.get_akt_sverka(supplier_id, date_from, date_to, lang), True
        return {**_map_akt(d, supplier_id, date_from, date_to), "_mock": mock}


supplier_service = SupplierService()
