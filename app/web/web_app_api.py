"""WebApp JSON API — MX Таъминотчи shaxsiy kabineti (Telegram botni aks ettiradi).

Auth: Telegram initData (X-Telegram-Init-Data) yoki ?session=<token> (/getsession) —
MX-Client-Bot bilan bir xil mexanizm (app/web/web_app_auth.py).
Data: ``SupplierService`` — bot ishlatadigan xuddi shu servis (faqat 1C).
1C javob bermasa → 503 {"code": "SERVICE_UNAVAILABLE"} (app/main.py dagi handler),
SPA foydalanuvchiga xatolik haqida xabar ko'rsatadi.
"""
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.database import async_session
from app.i18n import SUPPORTED_LANGS
from app.models import User, WebSession
from app.services.pdf import build_akt_pdf
from app.services.supplier_api import ServiceUnavailable, SupplierError, SupplierService
from app.web.web_app_auth import authenticate_webapp_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webapp/api")

svc = SupplierService()


# ── helpers ─────────────────────────────────────────────────────────────────
def _ser(obj: Any) -> Any:
    """Recursively convert date/datetime to display strings for JSON."""
    if isinstance(obj, dict):
        return {k: _ser(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ser(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.strftime("%d.%m.%Y %H:%M")
    if isinstance(obj, date):
        return obj.strftime("%d.%m.%Y")
    return obj


def _totals(rows, key: str = "total") -> list[dict]:
    """Valyutalar bo'yicha jamlangan summalar (aralash valyutani qo'shib bo'lmaydi)."""
    acc: dict[str, float] = {}
    for r in rows or []:
        cur = (r.get("currency") or "UZS").upper()
        acc[cur] = acc.get(cur, 0.0) + float(r.get(key) or 0)
    order = sorted(acc, key=lambda c: (c != "UZS", c))
    return [{"currency": c, "total": acc[c]} for c in order]


def _creds(auth: dict) -> tuple:
    cfg = auth["bot_config"]
    return (cfg["base_url"], cfg["one_c_login"], cfg["one_c_password"])


async def _get_user(telegram_id: int, bot_id: int) -> Optional[User]:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id, User.bot_id == bot_id)
        )
        return result.scalar_one_or_none()


async def _save_user(telegram_id: int, bot_id: int, phone_number: str,
                     supplier_id: str, language: Optional[str] = None):
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id, User.bot_id == bot_id)
        )
        user = result.scalar_one_or_none()
        if user:
            user.phone_number = phone_number
            user.client_id = supplier_id
            if language in SUPPORTED_LANGS:
                user.language = language
        else:
            session.add(User(
                telegram_id=telegram_id, phone_number=phone_number,
                client_id=supplier_id, bot_id=bot_id,
                language=language if language in SUPPORTED_LANGS else "uz",
            ))
        await session.commit()


def _lang(user: Optional[User]) -> str:
    """Foydalanuvchi tili (akt sverka PDF si shu tilda tayyorlanadi)."""
    return user.language if user and user.language in SUPPORTED_LANGS else "uz"


async def _supplier_and_lang(auth: dict) -> tuple[str, str]:
    user = await _get_user(auth["telegram_id"], auth["bot_id"])
    if not user or not user.client_id:
        raise HTTPException(status_code=400, detail="Аввал рўйхатдан ўтинг")
    return user.client_id, _lang(user)


async def _require_supplier(auth: dict) -> str:
    user = await _get_user(auth["telegram_id"], auth["bot_id"])
    if not user or not user.client_id:
        raise HTTPException(status_code=400, detail="Аввал рўйхатдан ўтинг")
    return user.client_id


# ── auth / session ──────────────────────────────────────────────────────────
@router.get("/user")
async def get_user(auth: dict = Depends(authenticate_webapp_user)):
    user = await _get_user(auth["telegram_id"], auth["bot_id"])
    return {
        "telegram_id": auth["telegram_id"],
        "first_name": auth["first_name"],
        "last_name": auth["last_name"],
        "username": auth["username"],
        "registered": bool(user and user.client_id),
        "phone_number": user.phone_number if user else None,
        "supplier_id": user.client_id if user else None,
        "language": (user.language if user and user.language in SUPPORTED_LANGS else "uz"),
        "company_name": auth["bot_config"]["company_name"],
    }


class RegisterRequest(BaseModel):
    phone_number: str
    language: Optional[str] = None


@router.post("/register")
async def register(req: RegisterRequest, auth: dict = Depends(authenticate_webapp_user)):
    """Login — botdagi bilan bir xil: checkNumber (APIService.register_device)."""
    phone = req.phone_number.lstrip("+").replace(" ", "").replace("-", "")
    result = await svc.check_number(*_creds(auth), phone, str(auth["telegram_id"]), auth["bot_id"])
    if not result or not result.get("id"):
        raise HTTPException(status_code=400, detail="Таъминотчи топилмади. Рақамни текшириб қайта уриниб кўринг.")
    supplier_id = str(result["id"])
    await _save_user(auth["telegram_id"], auth["bot_id"], phone, supplier_id, req.language)
    return {"success": True, "supplier_id": supplier_id, "name": result.get("name") or ""}


class LanguageRequest(BaseModel):
    language: str


@router.post("/language")
async def set_language(req: LanguageRequest, auth: dict = Depends(authenticate_webapp_user)):
    if req.language not in SUPPORTED_LANGS:
        raise HTTPException(status_code=400, detail="Нотўғри тил")
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == auth["telegram_id"], User.bot_id == auth["bot_id"])
        )
        user = result.scalar_one_or_none()
        if user:
            user.language = req.language
            await session.commit()
    return {"success": True, "language": req.language}


@router.post("/logout")
async def logout(auth: dict = Depends(authenticate_webapp_user)):
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == auth["telegram_id"], User.bot_id == auth["bot_id"])
        )
        user = result.scalar_one_or_none()
        if user:
            user.client_id = None
            user.phone_number = None
        await session.execute(
            delete(WebSession).where(
                WebSession.telegram_id == auth["telegram_id"],
                WebSession.bot_id == auth["bot_id"],
            )
        )
        await session.commit()
    return {"success": True}


# ── 👤 cabinet ──────────────────────────────────────────────────────────────
@router.get("/cabinet")
async def get_cabinet(auth: dict = Depends(authenticate_webapp_user)):
    user = await _get_user(auth["telegram_id"], auth["bot_id"])
    if not user or not user.client_id:
        raise HTTPException(status_code=400, detail="Аввал рўйхатдан ўтинг")
    supplier_id, lang, phone = user.client_id, _lang(user), user.phone_number or ""
    # Profil — asosiy manba (u olinmasa 503). Qolganlari yordamchi: bittasi
    # ishlamasa ham kabinet ochiladi, o'sha qiymat null bo'lib, "unavailable"
    # ro'yxatida qaysi bo'lim olinmagani qaytadi.
    cab = await svc.get_cabinet(*_creds(auth), supplier_id, phone=phone)

    unavailable: list[str] = []

    async def _part(coro, name: str):
        try:
            return await coro
        except (ServiceUnavailable, SupplierError) as e:
            logger.warning("webapp cabinet: %s olinmadi (%s)", name, e)
            unavailable.append(name)
            return None

    bal = await _part(svc.get_balance(*_creds(auth), supplier_id), "balance")
    bon = await _part(svc.get_bonuses(*_creds(auth), supplier_id), "bonuses")
    shipments = await _part(svc.get_shipments(*_creds(auth), supplier_id), "shipments")
    payments = await _part(svc.get_payments(*_creds(auth), supplier_id), "payments")
    returns = await _part(svc.get_returns(*_creds(auth), supplier_id), "returns")

    return _ser({
        **cab,
        "balance": bal["balance"] if bal else None,
        "balance_currency": bal["currency"] if bal else None,
        "balances": bal.get("balances") if bal else None,
        "balance_as_of": bal["as_of"] if bal else None,
        "bonus_remaining": bon["remaining"] if bon else None,
        "shipments_count": len(shipments) if shipments is not None else None,
        "shipments_total": sum(s["total"] for s in shipments) if shipments is not None else None,
        "shipments_totals": _totals(shipments) if shipments is not None else None,
        "payments_confirmed_total": (sum(p["amount"] for p in payments if p["status"] == "confirmed")
                                     if payments is not None else None),
        "payments_confirmed_totals": (_totals([p for p in payments if p["status"] == "confirmed"], "amount")
                                      if payments is not None else None),
        "pending_payments": sum(1 for p in payments if p["status"] == "pending") if payments else 0,
        "pending_returns": sum(1 for r in returns if r["status"] == "pending") if returns else 0,
        "unavailable": unavailable,
    })


# ── 💳 balance ──────────────────────────────────────────────────────────────
@router.get("/balance")
async def get_balance(auth: dict = Depends(authenticate_webapp_user)):
    supplier_id = await _require_supplier(auth)
    return _ser(await svc.get_balance(*_creds(auth), supplier_id))


# ── 💰 payments ─────────────────────────────────────────────────────────────
@router.get("/payments")
async def get_payments(auth: dict = Depends(authenticate_webapp_user)):
    supplier_id, lang = await _supplier_and_lang(auth)
    payments = await svc.get_payments(*_creds(auth), supplier_id)
    confirmed = [p for p in payments if p["status"] == "confirmed"]
    return _ser({
        "payments": payments,
        "totals": _totals(payments, "amount"),
        "confirmed_totals": _totals(confirmed, "amount"),
        "total": sum(p["amount"] for p in payments),
        "confirmed_total": sum(p["amount"] for p in confirmed),
        "pending_count": sum(1 for p in payments if p["status"] == "pending"),
    })


@router.post("/payments/{payment_id}/confirm")
async def confirm_payment(payment_id: int, auth: dict = Depends(authenticate_webapp_user)):
    supplier_id, lang = await _supplier_and_lang(auth)
    result = await svc.confirm_payment(*_creds(auth), supplier_id, payment_id,
                                       chat_id=str(auth["telegram_id"]), source="webapp")
    if not result or not result.get("success"):
        raise HTTPException(status_code=400, detail="Тасдиқлаш амалга ошмади")
    p = await svc.get_payment(*_creds(auth), supplier_id, payment_id)
    return _ser({**result, "payment": p})


# ── 📦 shipments ────────────────────────────────────────────────────────────
@router.get("/shipments")
async def get_shipments(auth: dict = Depends(authenticate_webapp_user)):
    supplier_id, lang = await _supplier_and_lang(auth)
    shipments = await svc.get_shipments(*_creds(auth), supplier_id)
    return _ser({
        "shipments": shipments,
        "totals": _totals(shipments),
        "total": sum(s["total"] for s in shipments),   # eski mijozlar uchun
        "count": len(shipments),
    })


# ── 🔄 returns ──────────────────────────────────────────────────────────────
@router.get("/returns")
async def get_returns(auth: dict = Depends(authenticate_webapp_user)):
    supplier_id, lang = await _supplier_and_lang(auth)
    returns = await svc.get_returns(*_creds(auth), supplier_id)
    return _ser({
        "returns": returns,
        "totals": _totals(returns),
        "total": sum(r["total"] for r in returns),
        "pending_count": sum(1 for r in returns if r["status"] == "pending"),
    })


@router.post("/returns/{return_id}/confirm")
async def confirm_return(return_id: int, auth: dict = Depends(authenticate_webapp_user)):
    supplier_id, lang = await _supplier_and_lang(auth)
    result = await svc.confirm_return(*_creds(auth), supplier_id, return_id,
                                      chat_id=str(auth["telegram_id"]), source="webapp")
    if not result or not result.get("success"):
        raise HTTPException(status_code=400, detail="Тасдиқлаш амалга ошмади")
    r = await svc.get_return(*_creds(auth), supplier_id, return_id)
    return _ser({**result, "return": r})


# ── 🎁 bonuses ──────────────────────────────────────────────────────────────
@router.get("/bonuses")
async def get_bonuses(auth: dict = Depends(authenticate_webapp_user)):
    supplier_id, lang = await _supplier_and_lang(auth)
    return _ser(await svc.get_bonuses(*_creds(auth), supplier_id))


# ── 💱 valyutalar (akt sverka uchun) ────────────────────────────────────────
@router.get("/currencies")
async def get_currencies(auth: dict = Depends(authenticate_webapp_user)):
    """getcry. 1C da tayyor bo'lmasa bo'sh ro'yxat — SPA valyuta tanlashni ko'rsatmaydi."""
    await _require_supplier(auth)
    try:
        return {"currencies": await svc.get_currencies(*_creds(auth))}
    except (ServiceUnavailable, SupplierError) as e:
        logger.warning("webapp: valyutalar olinmadi (%s)", e)
        return {"currencies": []}


# ── 📄 akt sverka ───────────────────────────────────────────────────────────
def _parse_period(date_from: Optional[str], date_to: Optional[str]) -> tuple[date, date]:
    today = date.today()
    try:
        d1 = date.fromisoformat(date_from) if date_from else today.replace(day=1)
        d2 = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        raise HTTPException(status_code=400, detail="Нотўғри сана формати (YYYY-MM-DD)")
    if d1 > d2:
        d1, d2 = d2, d1
    if (d2 - d1) > timedelta(days=1830):
        raise HTTPException(status_code=400, detail="Давр 5 йилдан ошмасин")
    return d1, d2


@router.get("/akt-sverka")
async def get_akt_sverka(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    cry_id: Optional[int] = None,
    cry: Optional[str] = None,
    auth: dict = Depends(authenticate_webapp_user),
):
    supplier_id, lang = await _supplier_and_lang(auth)
    d1, d2 = _parse_period(date_from, date_to)
    akt = await svc.get_akt_sverka(*_creds(auth), supplier_id, d1, d2,
                                   cry_id=cry_id, currency=cry or "UZS")
    return _ser({**akt, "date_from_iso": d1.isoformat(), "date_to_iso": d2.isoformat()})


@router.get("/akt-sverka/pdf")
async def get_akt_sverka_pdf(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    lang: Optional[str] = None,
    cry_id: Optional[int] = None,
    cry: Optional[str] = None,
    auth: dict = Depends(authenticate_webapp_user),
):
    supplier_id, user_lang = await _supplier_and_lang(auth)
    lang = lang if lang in SUPPORTED_LANGS else user_lang
    d1, d2 = _parse_period(date_from, date_to)
    akt = await svc.get_akt_sverka(*_creds(auth), supplier_id, d1, d2,
                                   cry_id=cry_id, currency=cry or "UZS")
    try:
        pdf_bytes = build_akt_pdf(akt, lang)
    except Exception as e:
        logger.error("webapp akt pdf failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="PDF тайёрлашда хатолик")
    suffix = f"_{cry}" if cry else ""
    filename = f"akt_sverka{suffix}_{d1.isoformat()}_{d2.isoformat()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
