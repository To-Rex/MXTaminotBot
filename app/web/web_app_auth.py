import hashlib
import hmac
import json
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from fastapi import Header, HTTPException, Request
from sqlalchemy import select

from app.database import async_session
from app.models import Bot, WebSession

logger = logging.getLogger(__name__)


def _init_data_fields(init_data: str) -> dict:
    """initData ni maydonlarga ajratadi (bo'sh qiymatlar ham saqlanadi)."""
    return {k: v[0] for k, v in urllib.parse.parse_qs(init_data, keep_blank_values=True).items()}


def _hmac_hex(token: str, data_check_string: str) -> str:
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()


def verify_telegram_init_data(init_data: str, bot_token: str) -> bool:
    """Telegram initData imzosini tekshirish (HMAC-SHA256).

    Ikkita variant sinaladi — Telegram mijozlari orasidagi farqlarga bardosh berish uchun:

    1. ``hash`` dan tashqari **barcha** maydonlar (standart usul);
    2. ``hash`` va ``signature`` dan tashqari — yangi mijozlar qo'shadigan
       ``signature`` maydoni hash hisoblangandan **keyin** qo'shilgan bo'lsa.

    Ikkalasi ham bot tokeni bilan imzolanadi, shuning uchun xavfsizlik pasaymaydi.
    ``keep_blank_values=True`` muhim: ``start_param=`` kabi bo'sh maydonlar ham imzoga kiradi.
    """
    return bool(_matching_variant(init_data, bot_token))


def _matching_variant(init_data: str, bot_token: str) -> Optional[str]:
    """Mos kelgan variant nomi (yoki None) — diagnostika uchun."""
    fields = _init_data_fields(init_data)
    hash_val = fields.pop("hash", None)
    if not hash_val or not bot_token:
        return None
    variants = {"standart": fields}
    if "signature" in fields:
        variants["signature'siz"] = {k: v for k, v in fields.items() if k != "signature"}
    for name, data in variants.items():
        dcs = "\n".join(f"{k}={data[k]}" for k in sorted(data))
        if hmac.compare_digest(_hmac_hex(bot_token, dcs), hash_val):
            return name
    return None


def diagnose_init_data(init_data: str, bots: list) -> dict:
    """Nega imzo mos kelmadi — server logi va /panel uchun tushunarli tavsif."""
    fields = _init_data_fields(init_data)
    info = {
        "fields": sorted(fields),
        "has_hash": "hash" in fields,
        "has_signature": "signature" in fields,
        "auth_date": fields.get("auth_date", ""),
        "matched_bot_id": None,
        "matched_variant": None,
    }
    for b in bots:
        variant = _matching_variant(init_data, b.token or "")
        if variant:
            info["matched_bot_id"], info["matched_variant"] = b.id, variant
            break
    if info["auth_date"].isdigit():
        age = int(datetime.now(timezone.utc).timestamp()) - int(info["auth_date"])
        info["age_seconds"] = age
    return info


def _parse_init_user(init_data: str) -> Optional[dict]:
    parsed = urllib.parse.parse_qs(init_data, keep_blank_values=True)
    user_raw = parsed.get("user", [None])[0]
    if not user_raw:
        return None
    try:
        return json.loads(user_raw)
    except json.JSONDecodeError:
        return None


def _bot_payload(bot: Bot) -> dict:
    return {
        "id": bot.id,
        "company_name": bot.company_name or "",
        "base_url": bot.base_url or "",
        "one_c_login": bot.one_c_login or "",
        "one_c_password": bot.one_c_password or "",
    }


async def _auth_by_session_token(token: str) -> Optional[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(WebSession).where(WebSession.token == token)
        )
        ws = result.scalar_one_or_none()
        if not ws:
            return None

        expires_at = ws.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            return None

        bot_result = await session.execute(select(Bot).where(Bot.id == ws.bot_id))
        bot = bot_result.scalar_one_or_none()
        if not bot:
            return None

        return {
            "telegram_id": int(ws.telegram_id),
            "first_name": ws.first_name or "",
            "last_name": ws.last_name or "",
            "username": ws.username or "",
            "bot_id": bot.id,
            "bot_config": _bot_payload(bot),
        }


async def authenticate_webapp_user(
    request: Request,
    x_init_data: Optional[str] = Header(default=None, alias="X-Telegram-Init-Data"),
) -> dict:
    session_token = request.query_params.get("session")
    if session_token:
        auth = await _auth_by_session_token(session_token)
        if not auth:
            raise HTTPException(status_code=401, detail="Sessiya yaroqsiz yoki muddati o'tgan")
        return auth

    if not x_init_data:
        raise HTTPException(status_code=401, detail="Auth required")

    bot_id_str = request.query_params.get("bot_id")
    if not bot_id_str:
        raise HTTPException(status_code=400, detail="bot_id required")

    try:
        bot_id = int(bot_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid bot_id")

    async with async_session() as session:
        result = await session.execute(select(Bot).where(Bot.id == bot_id))
        bot = result.scalar_one_or_none()

    if not bot:
        # Havoladagi bot_id bazada yo'q (eski havola, import'dan keyin ID o'zgargan
        # va h.k.) — imzo qaysi bot tokeniga mos kelsa, o'shani ishlatamiz.
        async with async_session() as session:
            all_bots = (await session.execute(select(Bot))).scalars().all()
        bot = next((b for b in all_bots if verify_telegram_init_data(x_init_data, b.token)), None)
        if bot is None:
            logger.warning("webapp auth: bot_id=%s bazada yo'q va imzo hech biriga mos kelmadi "
                           "(%d ta bot tekshirildi)", bot_id, len(all_bots))
            raise HTTPException(status_code=400, detail="Bot not found")
        logger.warning("webapp auth: bot_id=%s bazada yo'q — imzo bo'yicha bot #%s topildi",
                       bot_id, bot.id)
        return {
            "telegram_id": int((_parse_init_user(x_init_data) or {}).get("id", 0)),
            "first_name": (_parse_init_user(x_init_data) or {}).get("first_name", ""),
            "last_name": (_parse_init_user(x_init_data) or {}).get("last_name", ""),
            "username": (_parse_init_user(x_init_data) or {}).get("username", ""),
            "bot_id": bot.id,
            "bot_config": _bot_payload(bot),
        }

    if not verify_telegram_init_data(x_init_data, bot.token):
        # Havoladagi bot_id boshqa botniki bo'lishi mumkin (masalan menyu tugmasi
        # eski bot uchun sozlangan). Shuning uchun qolgan botlar tokenini ham
        # sinab ko'ramiz — imzo qaysi bot tokeni bilan mos kelsa, o'sha bot
        # konteksti ishlatiladi (imzo tekshiruvi baribir majburiy).
        async with async_session() as session:
            all_bots = (await session.execute(select(Bot))).scalars().all()
        match = next((b for b in all_bots if b.id != bot_id and verify_telegram_init_data(x_init_data, b.token)), None)
        if match is None:
            d = diagnose_init_data(x_init_data, all_bots)
            logger.error(
                "webapp auth XATO: initData imzosi hech bir bot tokeniga mos kelmadi.\n"
                "   havoladagi bot_id : %s (bazada %d ta bot: %s)\n"
                "   initData maydonlari: %s\n"
                "   hash bor: %s | signature bor: %s | auth_date: %s (%s soniya oldin)\n"
                "   Sabab odatda: (1) shu bot uchun panelda BOSHQA token saqlangan — "
                "panelda tokenni tekshiring; (2) WebApp boshqa botning menyu tugmasidan ochilgan.",
                bot_id, len(all_bots), ", ".join(f"#{b.id} {b.name}" for b in all_bots),
                ", ".join(d["fields"]), d["has_hash"], d["has_signature"],
                d["auth_date"] or "—", d.get("age_seconds", "?"),
            )
            raise HTTPException(status_code=401, detail="Invalid Telegram init data")
        logger.warning("webapp auth: havolada bot_id=%s edi, imzo bot_id=%s ga mos keldi",
                       bot_id, match.id)
        bot = match

    user_data = _parse_init_user(x_init_data)
    if not user_data or "id" not in user_data:
        raise HTTPException(status_code=401, detail="Invalid user data")

    return {
        "telegram_id": int(user_data["id"]),
        "first_name": user_data.get("first_name", ""),
        "last_name": user_data.get("last_name", ""),
        "username": user_data.get("username", ""),
        "bot_id": bot.id,
        "bot_config": _bot_payload(bot),
    }
