"""MX Таъминотчи Telegram bot — таъминотчининг шахсий кабинети.

Bo'limlar (TZ):
  1. Рўйхатдан ўтиш — тил танлаш (uz/ru) → телефон → 1C checkNumber → Telegram bog'lash
  2. 💰 Тўловлар — рўйхат, деталлар, тасдиқлаш, ҳолат
  3. 📦 Берилган юклар — рўйхат, детализация (товар, миқдор, сумма, сана)
  4. 🔄 Юк қайтариш — рўйхат, деталлар, тасдиқлаш, ҳолат
  5. 🎁 Бонуслар — ҳисобланган / фойдаланилган / қолган
  6. 💳 Баланс — жорий баланс
  7. 📄 Акт сверка — давр танлаш, кўриш, PDF
Data: ``SupplierService`` (1C HTTP-servis, docs/1C_SUPPLIER_API.md) — barcha
ma'lumot faqat 1C dan olinadi. 1C javob bermasa (404 / bo'sh javob / tarmoq
xatosi) foydalanuvchiga xatolik haqida xabar beriladi, tafsiloti /panel/api-logs da.
"""
import logging
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command, ExceptionTypeFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.config import WEBAPP_URL
from app.i18n import DEFAULT_LANG, SUPPORTED_LANGS, t
from app.models import User, WebSession
from app.services.pdf import build_akt_pdf
from app.services.supplier_api import ServiceUnavailable, SupplierError, SupplierService

logger = logging.getLogger(__name__)

SESSION_TTL_HOURS = 24 * 30  # brauzer sessiyasi (/getsession) — 30 kun

fmt_money = SupplierService.fmt_money
fmt_date = SupplierService.fmt_date

STATUS_ICON = {"pending": "⏳", "confirmed": "✅", "cancelled": "❌"}
AKT_ROWS_LIMIT = 20  # botda ko'rsatiladigan qatorlar; to'lig'i PDF da


def _both(key: str) -> set[str]:
    """Tugma matni ikkala tilda ham ushlansin (til almashganda ham ishlaydi)."""
    return {t(lang, key) for lang in SUPPORTED_LANGS}


class AktState(StatesGroup):
    waiting_period = State()


class LangState(StatesGroup):
    waiting_phone = State()


# ── keyboards ────────────────────────────────────────────────────────────────
def main_menu_keyboard(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "btn_cabinet")), KeyboardButton(text=t(lang, "btn_balance"))],
            [KeyboardButton(text=t(lang, "btn_payments")), KeyboardButton(text=t(lang, "btn_shipments"))],
            [KeyboardButton(text=t(lang, "btn_returns")), KeyboardButton(text=t(lang, "btn_bonuses"))],
            [KeyboardButton(text=t(lang, "btn_akt")), KeyboardButton(text=t(lang, "btn_language"))],
            [KeyboardButton(text=t(lang, "btn_logout"))],
        ],
        resize_keyboard=True,
    )


def phone_keyboard(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "btn_send_phone"), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def cancel_keyboard(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "btn_cancel"))]],
        resize_keyboard=True,
    )


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇺🇿 Ўзбекча", callback_data="lang_uz"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
    ]])


def create_router(
    bot_config: dict,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router()
    svc = SupplierService()

    bot_id = bot_config["id"]
    company = bot_config["company_name"] or "MX"
    creds = (bot_config["base_url"], bot_config["one_c_login"], bot_config["one_c_password"])

    # ── DB helpers ───────────────────────────────────────────────────────
    async def _get_user(telegram_id: int) -> Optional[User]:
        async with session_factory() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id, User.bot_id == bot_id)
            )
            return result.scalar_one_or_none()

    async def _save_user(telegram_id: int, phone_number: str, supplier_id: str, language: str):
        async with session_factory() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id, User.bot_id == bot_id)
            )
            user = result.scalar_one_or_none()
            if user:
                user.phone_number = phone_number
                user.client_id = supplier_id
                user.language = language
            else:
                session.add(User(
                    telegram_id=telegram_id, phone_number=phone_number,
                    client_id=supplier_id, bot_id=bot_id, language=language,
                ))
            await session.commit()

    async def _set_language(telegram_id: int, language: str):
        async with session_factory() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id, User.bot_id == bot_id)
            )
            user = result.scalar_one_or_none()
            if user:
                user.language = language
                await session.commit()

    async def _logout_user(telegram_id: int):
        async with session_factory() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id, User.bot_id == bot_id)
            )
            user = result.scalar_one_or_none()
            if user:
                user.client_id = None
                user.phone_number = None
            await session.execute(
                delete(WebSession).where(
                    WebSession.telegram_id == telegram_id,
                    WebSession.bot_id == bot_id,
                )
            )
            await session.commit()

    async def _lang_of(telegram_id: int, state: Optional[FSMContext] = None) -> str:
        user = await _get_user(telegram_id)
        if user and user.language in SUPPORTED_LANGS:
            return user.language
        if state is not None:
            data = await state.get_data()
            if data.get("lang") in SUPPORTED_LANGS:
                return data["lang"]
        return DEFAULT_LANG

    async def _require_supplier(message: Message) -> tuple[Optional[str], str]:
        """(supplier_id, lang) — ro'yxatdan o'tmagan bo'lsa telefon so'raladi."""
        user = await _get_user(message.from_user.id)
        lang = user.language if user and user.language in SUPPORTED_LANGS else DEFAULT_LANG
        if not user or not user.client_id:
            logger.warning("session missing: tg=%s bot=%s (msg)", message.from_user.id, bot_id)
            await message.answer(t(lang, "relogin"), reply_markup=phone_keyboard(lang))
            return None, lang
        return user.client_id, lang

    async def _supplier_from_callback(callback: CallbackQuery) -> tuple[Optional[str], str]:
        user = await _get_user(callback.from_user.id)
        lang = user.language if user and user.language in SUPPORTED_LANGS else DEFAULT_LANG
        if not user or not user.client_id:
            logger.warning("session missing: tg=%s bot=%s (callback %s)",
                           callback.from_user.id, bot_id, callback.data)
            await callback.answer(t(lang, "relogin_alert"), show_alert=True)
            try:
                await callback.message.answer(t(lang, "relogin"), reply_markup=phone_keyboard(lang))
            except Exception:
                pass
            return None, lang
        return user.client_id, lang

    async def _answer_or_edit(target: Message, text: str, kb=None, edit: bool = False):
        if edit:
            try:
                await target.edit_text(text, reply_markup=kb)
                return
            except Exception:
                pass
        await target.answer(text, reply_markup=kb)


    # ════════════════════════════════════════════════════════════════════
    # 1. РЎЙХАТДАН ЎТИШ (TZ 1)
    # ════════════════════════════════════════════════════════════════════
    @router.message(Command("start"))
    async def start_handler(message: Message, state: FSMContext):
        # /start har doim avval til tanlashdan boshlanadi (TZ 1)
        await state.clear()
        await message.answer(t(DEFAULT_LANG, "choose_language"), reply_markup=language_keyboard())

    @router.callback_query(F.data.startswith("lang_"))
    async def language_callback(callback: CallbackQuery, state: FSMContext):
        lang = callback.data.split("_", 1)[1]
        if lang not in SUPPORTED_LANGS:
            await callback.answer()
            return
        await callback.answer(t(lang, "language_set"))
        user = await _get_user(callback.from_user.id)
        if user and user.client_id:
            # tizimdagi foydalanuvchi tilni almashtirdi
            await _set_language(callback.from_user.id, lang)
            try:
                await callback.message.edit_text(t(lang, "language_set"))
            except Exception:
                pass
            await callback.message.answer(
                t(lang, "welcome_back", company=company),
                reply_markup=main_menu_keyboard(lang),
            )
            return
        # ro'yxatdan o'tish oqimi: tilni eslab qolib telefon so'raymiz
        await state.update_data(lang=lang)
        await state.set_state(LangState.waiting_phone)
        try:
            await callback.message.edit_text(t(lang, "language_set"))
        except Exception:
            pass
        await callback.message.answer(
            t(lang, "welcome_new", company=company),
            reply_markup=phone_keyboard(lang),
        )

    @router.message(F.text.in_(_both("btn_language")))
    async def language_menu_handler(message: Message):
        supplier_id, lang = await _require_supplier(message)
        if not supplier_id:
            return
        await message.answer(t(lang, "choose_language"), reply_markup=language_keyboard())

    @router.message(F.contact)
    async def contact_handler(message: Message, state: FSMContext):
        if message.contact is None:
            return
        lang = await _lang_of(message.from_user.id, state)
        await state.clear()

        phone = message.contact.phone_number.lstrip("+").replace(" ", "").replace("-", "")
        logger.info("🤖 Bot[%s] contact: telegram_id=%s phone=%s", bot_id, message.from_user.id, phone)

        result = await svc.check_number(*creds, phone, str(message.chat.id), bot_id)
        logger.info("🤖 Bot[%s] checkNumber result: %s", bot_id,
                    f"id={result.get('id')}" if result else "None")

        if result and result.get("id"):
            supplier_id = str(result["id"])
            await _save_user(message.from_user.id, phone, supplier_id, lang)
            name = result.get("name") or ""
            text = t(lang, "registered_ok", name=f", {name}" if name else "")
            await message.answer(text, reply_markup=main_menu_keyboard(lang))
        else:
            await message.answer(t(lang, "supplier_not_found"), reply_markup=phone_keyboard(lang))

    # ── WebApp: /getsession havolasi va 📱 Веб-кабинет tugmasi ──────────
    async def _new_web_session_url(message: Message) -> str:
        """WebSession yaratib, webapp havolasini qaytaradi (30 kunlik token)."""
        token = secrets.token_urlsafe(32)
        async with session_factory() as db:
            db.add(WebSession(
                token=token,
                telegram_id=message.from_user.id,
                bot_id=bot_id,
                first_name=message.from_user.first_name or "",
                last_name=message.from_user.last_name or "",
                username=message.from_user.username or "",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS),
            ))
            await db.commit()
        return f"{WEBAPP_URL.rstrip('/')}/webapp?bot_id={bot_id}&session={token}"

    @router.message(Command("getsession"))
    async def getsession_handler(message: Message):
        lang = await _lang_of(message.from_user.id)
        if not WEBAPP_URL:
            await message.answer(t(lang, "getsession_no_url"))
            return
        url = await _new_web_session_url(message)
        await message.answer(
            t(lang, "getsession_text", url=url, days=SESSION_TTL_HOURS // 24),
            disable_web_page_preview=True,
        )

    # ── logout ───────────────────────────────────────────────────────────
    async def _ask_logout_confirm(message: Message, state: FSMContext, lang: str):
        await state.clear()
        await message.answer(
            t(lang, "logout_confirm"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=t(lang, "logout_yes"), callback_data="logout_yes"),
                InlineKeyboardButton(text=t(lang, "logout_no"), callback_data="logout_no"),
            ]]),
        )

    @router.message(Command("logout"))
    async def logout_command_handler(message: Message, state: FSMContext):
        lang = await _lang_of(message.from_user.id, state)
        await _ask_logout_confirm(message, state, lang)

    @router.message(F.text.in_(_both("btn_logout")))
    async def logout_button_handler(message: Message, state: FSMContext):
        lang = await _lang_of(message.from_user.id, state)
        await _ask_logout_confirm(message, state, lang)

    @router.callback_query(F.data == "logout_yes")
    async def logout_yes_callback(callback: CallbackQuery, state: FSMContext):
        lang = await _lang_of(callback.from_user.id, state)
        await state.clear()
        await _logout_user(callback.from_user.id)
        await callback.answer()
        try:
            await callback.message.edit_text(t(lang, "logged_out"))
        except Exception:
            pass
        await callback.message.answer(t(lang, "login_again"), reply_markup=phone_keyboard(lang))

    @router.callback_query(F.data == "logout_no")
    async def logout_no_callback(callback: CallbackQuery):
        lang = await _lang_of(callback.from_user.id)
        await callback.answer()
        try:
            await callback.message.edit_text(t(lang, "stay_in"))
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════════════
    # 👤 КАБИНЕТ — getClientInfo (MX-Client-Bot bilan umumiy, 1C da tayyor)
    # ════════════════════════════════════════════════════════════════════
    @router.message(F.text.in_(_both("btn_cabinet")))
    async def cabinet_handler(message: Message):
        supplier_id, lang = await _require_supplier(message)
        if not supplier_id:
            return
        user = await _get_user(message.from_user.id)
        phone = (user.phone_number if user else "") or ""
        cab = await svc.get_cabinet(*creds, supplier_id, phone=phone)
        bal = await svc.get_balance(*creds, supplier_id)
        bon = await svc.get_bonuses(*creds, supplier_id)
        shipments = await svc.get_shipments(*creds, supplier_id)
        payments = await svc.get_payments(*creds, supplier_id)
        returns = await svc.get_returns(*creds, supplier_id)
        pending = (sum(1 for p in payments if p["status"] == "pending")
                   + sum(1 for r in returns if r["status"] == "pending"))
        confirmed_paid = sum(p["amount"] for p in payments if p["status"] == "confirmed")

        lines = [
            t(lang, "cabinet_title"), "",
            f"▪️ <b>{t(lang, 'f_name')}:</b> {cab['name']}",
            f"▪️ <b>{t(lang, 'f_phone')}:</b> +{cab['phone']}" if cab.get("phone") else f"▪️ <b>{t(lang, 'f_phone')}:</b> —",
            f"▪️ <b>{t(lang, 'f_status')}:</b> {cab['status']}",
            f"▪️ <b>{t(lang, 'f_supplier_id')}:</b> <code>{cab['supplier_id']}</code>",
            f"▪️ <b>{t(lang, 'f_registered')}:</b> {cab['registered_at']}",
            "",
            f"<b>{t(lang, 'cab_summary')}</b>",
            f"📦 {t(lang, 'cab_shipments')}: {len(shipments)} • {fmt_money(sum(s['total'] for s in shipments))}",
            f"💰 {t(lang, 'cab_payments')}: {fmt_money(confirmed_paid)}",
            f"🎁 {t(lang, 'bonus_remaining')}: {fmt_money(bon['remaining'])}",
            f"{'🟢' if bal['balance'] >= 0 else '🔴'} {t(lang, 'balance_current')}: <b>{fmt_money(bal['balance'])}</b>",
        ]
        if pending:
            lines.append(f"⏳ {t(lang, 'cab_pending')}: {pending}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_payments"), callback_data="payments_list"),
             InlineKeyboardButton(text=t(lang, "btn_returns"), callback_data="returns_list")],
            [InlineKeyboardButton(text=t(lang, "btn_shipments"), callback_data="shipments_list")],
        ])
        await message.answer("\n".join(lines), reply_markup=kb)

    # ════════════════════════════════════════════════════════════════════
    # 2. 💰 ТЎЛОВЛАР (TZ 2.1)
    # ════════════════════════════════════════════════════════════════════
    def _method_label(lang: str, method: str) -> str:
        return t(lang, {"cash": "pm_cash", "transfer": "pm_transfer",
                        "card": "pm_card"}.get(method, "pm_other"))

    def _payments_view(lang: str, payments: list[dict]) -> tuple[str, Optional[InlineKeyboardMarkup]]:
        lines = [t(lang, "payments_title"), ""]
        if not payments:
            lines.append(t(lang, "payments_empty"))
            return "\n".join(lines), None
        total = sum(p["amount"] for p in payments)
        pending = [p for p in payments if p["status"] == "pending"]
        lines.append(t(lang, "payments_total", total=fmt_money(total), count=len(payments)))
        if pending:
            lines.append(f"{STATUS_ICON['pending']} {t(lang, 'st_pending')}: {len(pending)}")
        lines += ["", t(lang, "payments_pick")]
        buttons = [[InlineKeyboardButton(
            text=f"{STATUS_ICON[p['status']]} {fmt_date(p['date'])} — {fmt_money(p['amount'])}",
            callback_data=f"pmt_{p['payment_id']}",
        )] for p in payments[:15]]
        return "\n".join(l for l in lines if l is not None), InlineKeyboardMarkup(inline_keyboard=buttons)

    def _payment_text(lang: str, p: dict) -> str:
        lines = [
            t(lang, "payment_detail_title"), "",
            f"▪️ <b>{t(lang, 'f_doc')}:</b> <code>{p['doc_number']}</code>",
            f"▪️ <b>{t(lang, 'f_date')}:</b> {fmt_date(p['date'])}",
            f"▪️ <b>{t(lang, 'f_amount')}:</b> {fmt_money(p['amount'])}",
            f"▪️ <b>{t(lang, 'f_method')}:</b> {_method_label(lang, p['method'])}",
            f"▪️ <b>{t(lang, 'f_status')}:</b> {t(lang, 'st_' + p['status'])}",
        ]
        if p.get("confirmed_at"):
            lines.append(f"▪️ {p['confirmed_at'].strftime('%d.%m.%Y %H:%M')}")
        if p.get("note"):
            lines.append(f"▪️ <b>{t(lang, 'f_note')}:</b> {p['note']}")
        return "\n".join(lines)

    def _payment_kb(lang: str, p: dict) -> InlineKeyboardMarkup:
        rows = []
        if p["status"] == "pending":
            rows.append([InlineKeyboardButton(
                text=t(lang, "btn_confirm_payment"), callback_data=f"pmtc_{p['payment_id']}",
            )])
        rows.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="payments_list")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @router.message(F.text.in_(_both("btn_payments")))
    async def payments_handler(message: Message):
        supplier_id, lang = await _require_supplier(message)
        if not supplier_id:
            return
        payments = await svc.get_payments(*creds, supplier_id)
        text, kb = _payments_view(lang, payments)
        await message.answer(text, reply_markup=kb)

    @router.callback_query(F.data == "payments_list")
    async def payments_list_callback(callback: CallbackQuery):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        await callback.answer()
        payments = await svc.get_payments(*creds, supplier_id)
        text, kb = _payments_view(lang, payments)
        await _answer_or_edit(callback.message, text, kb, edit=True)

    @router.callback_query(F.data.startswith("pmt_"))
    async def payment_detail_callback(callback: CallbackQuery):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        payment_id = int(callback.data.split("_", 1)[1])
        p = await svc.get_payment(*creds, supplier_id, payment_id)
        if not p:
            await callback.answer(t(lang, "not_found"), show_alert=True)
            return
        await callback.answer()
        await _answer_or_edit(callback.message, _payment_text(lang, p), _payment_kb(lang, p), edit=True)

    @router.callback_query(F.data.startswith("pmtc_"))
    async def payment_confirm_callback(callback: CallbackQuery):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        payment_id = int(callback.data.split("_", 1)[1])
        result = await svc.confirm_payment(*creds, supplier_id, payment_id,
                                           chat_id=str(callback.from_user.id))
        if not result or not result.get("success"):
            await callback.answer(t(lang, "confirm_failed"), show_alert=True)
            return
        await callback.answer("✅")
        p = await svc.get_payment(*creds, supplier_id, payment_id)
        if p:
            await _answer_or_edit(callback.message, _payment_text(lang, p), _payment_kb(lang, p), edit=True)
            await callback.message.answer(
                t(lang, "payment_confirmed", doc=p["doc_number"])
            )

    # ════════════════════════════════════════════════════════════════════
    # 3. 📦 БЕРИЛГАН ЮКЛАР (TZ 2.2)
    # ════════════════════════════════════════════════════════════════════
    def _shipments_view(lang: str, shipments: list[dict]) -> tuple[str, Optional[InlineKeyboardMarkup]]:
        lines = [t(lang, "shipments_title"), ""]
        if not shipments:
            lines.append(t(lang, "shipments_empty"))
            return "\n".join(lines), None
        total = sum(s["total"] for s in shipments)
        lines.append(t(lang, "shipments_total", total=fmt_money(total), count=len(shipments)))
        lines += ["", t(lang, "shipments_pick")]
        buttons = [[InlineKeyboardButton(
            text=f"📦 {fmt_date(s['date'])} • {s['doc_number']} — {fmt_money(s['total'])}",
            callback_data=f"shp_{s['shipment_id']}",
        )] for s in shipments[:15]]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)

    def _shipment_text(lang: str, s: dict) -> str:
        lines = [
            t(lang, "shipment_detail_title"), "",
            f"▪️ <b>{t(lang, 'f_doc')}:</b> <code>{s['doc_number']}</code>",
            f"▪️ <b>{t(lang, 'f_date')}:</b> {fmt_date(s['date'])}",
        ]
        if s.get("warehouse"):
            lines.append(f"▪️ {s['warehouse']}")
        lines += ["", f"<b>{t(lang, 'products_header')}</b>"]
        for p in s["products"]:
            lines.append(f"  • {p['name']} × {p['qty']} — {fmt_money(p['sum'])}")
        lines += ["", f"💵 <b>{t(lang, 'f_total')}:</b> {fmt_money(s['total'])}"]
        return "\n".join(lines)

    @router.message(F.text.in_(_both("btn_shipments")))
    async def shipments_handler(message: Message):
        supplier_id, lang = await _require_supplier(message)
        if not supplier_id:
            return
        shipments = await svc.get_shipments(*creds, supplier_id)
        text, kb = _shipments_view(lang, shipments)
        await message.answer(text, reply_markup=kb)

    @router.callback_query(F.data == "shipments_list")
    async def shipments_list_callback(callback: CallbackQuery):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        await callback.answer()
        shipments = await svc.get_shipments(*creds, supplier_id)
        text, kb = _shipments_view(lang, shipments)
        await _answer_or_edit(callback.message, text, kb, edit=True)

    @router.callback_query(F.data.startswith("shp_"))
    async def shipment_detail_callback(callback: CallbackQuery):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        shipment_id = int(callback.data.split("_", 1)[1])
        s = await svc.get_shipment(*creds, supplier_id, shipment_id)
        if not s:
            await callback.answer(t(lang, "not_found"), show_alert=True)
            return
        await callback.answer()
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="shipments_list"),
        ]])
        await _answer_or_edit(callback.message, _shipment_text(lang, s), kb, edit=True)

    # ════════════════════════════════════════════════════════════════════
    # 4. 🔄 ЮК ҚАЙТАРИШ (TZ 2.3)
    # ════════════════════════════════════════════════════════════════════
    def _returns_view(lang: str, returns: list[dict]) -> tuple[str, Optional[InlineKeyboardMarkup]]:
        lines = [t(lang, "returns_title"), ""]
        if not returns:
            lines.append(t(lang, "returns_empty"))
            return "\n".join(lines), None
        pending = [r for r in returns if r["status"] == "pending"]
        if pending:
            lines.append(f"{STATUS_ICON['pending']} {t(lang, 'st_pending')}: {len(pending)}")
        lines += ["", t(lang, "returns_pick")]
        buttons = [[InlineKeyboardButton(
            text=f"{STATUS_ICON[r['status']]} {fmt_date(r['date'])} • {r['doc_number']} — {fmt_money(r['total'])}",
            callback_data=f"ret_{r['return_id']}",
        )] for r in returns[:15]]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)

    def _return_text(lang: str, r: dict) -> str:
        lines = [
            t(lang, "return_detail_title"), "",
            f"▪️ <b>{t(lang, 'f_doc')}:</b> <code>{r['doc_number']}</code>",
            f"▪️ <b>{t(lang, 'f_date')}:</b> {fmt_date(r['date'])}",
            f"▪️ <b>{t(lang, 'f_status')}:</b> {t(lang, 'st_' + r['status'])}",
        ]
        if r.get("confirmed_at"):
            lines.append(f"▪️ {r['confirmed_at'].strftime('%d.%m.%Y %H:%M')}")
        if r.get("reason"):
            lines.append(f"▪️ <b>{t(lang, 'f_reason')}:</b> {r['reason']}")
        lines += ["", f"<b>{t(lang, 'products_header')}</b>"]
        for p in r["products"]:
            lines.append(f"  • {p['name']} × {p['qty']} — {fmt_money(p['sum'])}")
        lines += ["", f"💵 <b>{t(lang, 'f_total')}:</b> {fmt_money(r['total'])}"]
        return "\n".join(lines)

    def _return_kb(lang: str, r: dict) -> InlineKeyboardMarkup:
        rows = []
        if r["status"] == "pending":
            rows.append([InlineKeyboardButton(
                text=t(lang, "btn_confirm_return"), callback_data=f"retc_{r['return_id']}",
            )])
        rows.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="returns_list")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @router.message(F.text.in_(_both("btn_returns")))
    async def returns_handler(message: Message):
        supplier_id, lang = await _require_supplier(message)
        if not supplier_id:
            return
        returns = await svc.get_returns(*creds, supplier_id)
        text, kb = _returns_view(lang, returns)
        await message.answer(text, reply_markup=kb)

    @router.callback_query(F.data == "returns_list")
    async def returns_list_callback(callback: CallbackQuery):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        await callback.answer()
        returns = await svc.get_returns(*creds, supplier_id)
        text, kb = _returns_view(lang, returns)
        await _answer_or_edit(callback.message, text, kb, edit=True)

    @router.callback_query(F.data.startswith("ret_"))
    async def return_detail_callback(callback: CallbackQuery):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        return_id = int(callback.data.split("_", 1)[1])
        r = await svc.get_return(*creds, supplier_id, return_id)
        if not r:
            await callback.answer(t(lang, "not_found"), show_alert=True)
            return
        await callback.answer()
        await _answer_or_edit(callback.message, _return_text(lang, r), _return_kb(lang, r), edit=True)

    @router.callback_query(F.data.startswith("retc_"))
    async def return_confirm_callback(callback: CallbackQuery):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        return_id = int(callback.data.split("_", 1)[1])
        result = await svc.confirm_return(*creds, supplier_id, return_id,
                                          chat_id=str(callback.from_user.id))
        if not result or not result.get("success"):
            await callback.answer(t(lang, "confirm_failed"), show_alert=True)
            return
        await callback.answer("✅")
        r = await svc.get_return(*creds, supplier_id, return_id)
        if r:
            await _answer_or_edit(callback.message, _return_text(lang, r), _return_kb(lang, r), edit=True)
            await callback.message.answer(
                t(lang, "return_confirmed", doc=r["doc_number"])
            )

    # ════════════════════════════════════════════════════════════════════
    # 5. 🎁 БОНУСЛАР (TZ 2.4)
    # ════════════════════════════════════════════════════════════════════
    @router.message(F.text.in_(_both("btn_bonuses")))
    async def bonuses_handler(message: Message):
        supplier_id, lang = await _require_supplier(message)
        if not supplier_id:
            return
        b = await svc.get_bonuses(*creds, supplier_id)
        lines = [
            t(lang, "bonuses_title"), "",
            f"➕ <b>{t(lang, 'bonus_accrued')}:</b> {fmt_money(b['accrued'])}",
            f"➖ <b>{t(lang, 'bonus_used')}:</b> {fmt_money(b['used'])}",
            f"🎁 <b>{t(lang, 'bonus_remaining')}:</b> {fmt_money(b['remaining'])}",
        ]
        if b["items"]:
            lines += ["", f"<b>{t(lang, 'bonus_history')}</b>"]
            for i in b["items"][:15]:
                sign = "➕" if i["kind"] == "accrued" else "➖"
                lines.append(f"{sign} {fmt_date(i['date'])} — {fmt_money(i['amount'])}"
                             + (f"\n   <i>{i['note']}</i>" if i.get("note") else ""))
        else:
            lines += ["", t(lang, "bonuses_empty")]
        await message.answer("\n".join(lines))

    # ════════════════════════════════════════════════════════════════════
    # 6. 💳 БАЛАНС (TZ 2.5)
    # ════════════════════════════════════════════════════════════════════
    @router.message(F.text.in_(_both("btn_balance")))
    async def balance_handler(message: Message):
        supplier_id, lang = await _require_supplier(message)
        if not supplier_id:
            return
        b = await svc.get_balance(*creds, supplier_id)
        icon = "🟢" if b["balance"] >= 0 else "🔴"
        hint = t(lang, "balance_we_owe") if b["balance"] >= 0 else t(lang, "balance_you_owe")
        lines = [
            t(lang, "balance_title"), "",
            f"{icon} <b>{t(lang, 'balance_current')}:</b> {fmt_money(b['balance'])}",
            "",
            hint,
            f"🕘 {t(lang, 'balance_as_of')}: {b['as_of'].strftime('%d.%m.%Y %H:%M')}",
        ]
        await message.answer("\n".join(lines))

    # ════════════════════════════════════════════════════════════════════
    # 7. 📄 АКТ СВЕРКА (TZ 2.6)
    # ════════════════════════════════════════════════════════════════════
    def _akt_period_kb(lang: str) -> InlineKeyboardMarkup:
        today = date.today()
        m_start = today.replace(day=1)
        pm_end = m_start - timedelta(days=1)
        pm_start = pm_end.replace(day=1)
        m3_start = (m_start - timedelta(days=62)).replace(day=1)
        y_start = today.replace(month=1, day=1)

        def cb(d1: date, d2: date) -> str:
            return f"akt_{d1.isoformat()}_{d2.isoformat()}"

        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "period_this_month"), callback_data=cb(m_start, today)),
             InlineKeyboardButton(text=t(lang, "period_prev_month"), callback_data=cb(pm_start, pm_end))],
            [InlineKeyboardButton(text=t(lang, "period_3m"), callback_data=cb(m3_start, today)),
             InlineKeyboardButton(text=t(lang, "period_year"), callback_data=cb(y_start, today))],
            [InlineKeyboardButton(text=t(lang, "period_custom"), callback_data="akt_custom")],
        ])

    async def _show_akt_menu(target: Message, lang: str, edit: bool = False):
        text = t(lang, "akt_title") + "\n\n" + t(lang, "akt_choose_period")
        await _answer_or_edit(target, text, _akt_period_kb(lang), edit=edit)

    @router.message(F.text.in_(_both("btn_akt")))
    async def akt_handler(message: Message, state: FSMContext):
        supplier_id, lang = await _require_supplier(message)
        if not supplier_id:
            return
        await state.clear()
        await _show_akt_menu(message, lang)

    @router.callback_query(F.data == "akt_menu")
    async def akt_menu_callback(callback: CallbackQuery, state: FSMContext):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        await state.clear()
        await callback.answer()
        await _show_akt_menu(callback.message, lang, edit=True)

    def _akt_text(lang: str, akt: dict) -> str:
        lines = [
            t(lang, "akt_title"), "",
            f"📅 <b>{t(lang, 'akt_period')}:</b> {fmt_date(akt['date_from'])} — {fmt_date(akt['date_to'])}",
            f"▪️ <b>{t(lang, 'akt_opening')}:</b> {fmt_money(akt['opening_balance'])}",
            f"➕ <b>{t(lang, 'akt_debit')}:</b> {fmt_money(akt['total_debit'])}",
            f"➖ <b>{t(lang, 'akt_credit')}:</b> {fmt_money(akt['total_credit'])}",
            f"💳 <b>{t(lang, 'akt_closing')}:</b> {fmt_money(akt['closing_balance'])}",
            "",
        ]
        rows = akt["rows"]
        if not rows:
            lines.append(t(lang, "akt_empty"))
        else:
            lines.append(f"<b>{t(lang, 'akt_rows')}:</b>")
            for r in rows[:AKT_ROWS_LIMIT]:
                amount = f"+{fmt_money(r['debit'])}" if r["debit"] else f"−{fmt_money(r['credit'])}"
                lines.append(f"• {fmt_date(r['date'])} • {r['doc']} • {r['note']} — <b>{amount}</b>")
            if len(rows) > AKT_ROWS_LIMIT:
                lines.append(t(lang, "akt_more_rows", n=len(rows) - AKT_ROWS_LIMIT))
        return "\n".join(lines)

    def _akt_kb(lang: str, d1: date, d2: date) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_akt_pdf"),
                                  callback_data=f"aktpdf_{d1.isoformat()}_{d2.isoformat()}")],
            [InlineKeyboardButton(text=t(lang, "btn_akt_period"), callback_data="akt_menu")],
        ])

    async def _show_akt(target: Message, supplier_id: str, lang: str,
                        d1: date, d2: date, edit: bool = False):
        akt = await svc.get_akt_sverka(*creds, supplier_id, d1, d2)
        await _answer_or_edit(target, _akt_text(lang, akt), _akt_kb(lang, d1, d2), edit=edit)

    @router.callback_query(F.data == "akt_custom")
    async def akt_custom_callback(callback: CallbackQuery, state: FSMContext):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        await state.set_state(AktState.waiting_period)
        await callback.answer()
        await callback.message.answer(t(lang, "akt_custom_prompt"), reply_markup=cancel_keyboard(lang))

    @router.message(AktState.waiting_period, F.text.in_(_both("btn_cancel")))
    async def akt_custom_cancel(message: Message, state: FSMContext):
        _, lang = await _require_supplier(message)
        await state.clear()
        await message.answer(t(lang, "cancelled"), reply_markup=main_menu_keyboard(lang))

    @router.message(AktState.waiting_period)
    async def akt_custom_entered(message: Message, state: FSMContext):
        supplier_id, lang = await _require_supplier(message)
        if not supplier_id:
            await state.clear()
            return
        m = re.match(
            r"^\s*(\d{2})\.(\d{2})\.(\d{4})\s*[-—]\s*(\d{2})\.(\d{2})\.(\d{4})\s*$",
            message.text or "",
        )
        if not m:
            await message.answer(t(lang, "akt_bad_period"))
            return
        try:
            d1 = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            d2 = date(int(m.group(6)), int(m.group(5)), int(m.group(4)))
        except ValueError:
            await message.answer(t(lang, "akt_bad_period"))
            return
        if d1 > d2:
            d1, d2 = d2, d1
        await state.clear()
        await message.answer(t(lang, "menu_title"), reply_markup=main_menu_keyboard(lang))
        await _show_akt(message, supplier_id, lang, d1, d2)

    @router.callback_query(F.data.startswith("aktpdf_"))
    async def akt_pdf_callback(callback: CallbackQuery):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        _, d1s, d2s = callback.data.split("_")
        d1, d2 = date.fromisoformat(d1s), date.fromisoformat(d2s)
        await callback.answer("📄 …")
        akt = await svc.get_akt_sverka(*creds, supplier_id, d1, d2)
        try:
            pdf_bytes = build_akt_pdf(akt, lang)
        except Exception as e:
            logger.error("akt pdf failed: %s", e, exc_info=True)
            await callback.message.answer(t(lang, "akt_pdf_failed"))
            return
        filename = f"akt_sverka_{d1.isoformat()}_{d2.isoformat()}.pdf"
        await callback.message.answer_document(
            BufferedInputFile(pdf_bytes, filename=filename),
            caption=t(lang, "akt_pdf_caption", date_from=fmt_date(d1), date_to=fmt_date(d2)),
        )

    @router.callback_query(F.data.startswith("akt_"))
    async def akt_period_callback(callback: CallbackQuery):
        supplier_id, lang = await _supplier_from_callback(callback)
        if not supplier_id:
            return
        parts = callback.data.split("_")  # akt_<from>_<to>
        if len(parts) != 3:
            await callback.answer()
            return
        try:
            d1, d2 = date.fromisoformat(parts[1]), date.fromisoformat(parts[2])
        except ValueError:
            await callback.answer()
            return
        await callback.answer()
        await _show_akt(callback.message, supplier_id, lang, d1, d2, edit=True)

    # ── 1C service errors → friendly messages (one place for all handlers) ─
    async def _reply_error(event: ErrorEvent, key: str, message_text: str = "", alert: bool = False):
        upd = event.update
        tg_id = None
        if upd.callback_query:
            tg_id = upd.callback_query.from_user.id
        elif upd.message:
            tg_id = upd.message.from_user.id
        lang = DEFAULT_LANG
        if tg_id:
            user = await _get_user(tg_id)
            if user and user.language in SUPPORTED_LANGS:
                lang = user.language
        text = message_text or t(lang, key)
        try:
            if upd.callback_query:
                if alert:
                    await upd.callback_query.answer(
                        text.replace("<b>", "").replace("</b>", "")[:190], show_alert=True)
                else:
                    await upd.callback_query.answer()
                    await upd.callback_query.message.answer(text, reply_markup=main_menu_keyboard(lang))
            elif upd.message:
                await upd.message.answer(text, reply_markup=main_menu_keyboard(lang))
        except Exception as e:  # never let the error handler itself explode
            logger.debug("error reply failed: %s", e)

    @router.errors(ExceptionTypeFilter(ServiceUnavailable))
    async def on_service_unavailable(event: ErrorEvent):
        exc: ServiceUnavailable = event.exception  # type: ignore[assignment]
        logger.warning("1C service unavailable: %s (%s)", exc.endpoint, exc.reason)
        await _reply_error(event, "service_error")

    @router.errors(ExceptionTypeFilter(SupplierError))
    async def on_supplier_error(event: ErrorEvent):
        exc: SupplierError = event.exception  # type: ignore[assignment]
        logger.warning("1C error %s: %s (%s)", exc.code, exc.message, exc.endpoint)
        await _reply_error(event, "", message_text=f"❌ {exc.message}", alert=True)

    # ── generic cancel outside of a state ───────────────────────────────
    @router.message(F.text.in_(_both("btn_cancel")))
    async def cancel_any(message: Message, state: FSMContext):
        lang = await _lang_of(message.from_user.id, state)
        await state.clear()
        await message.answer(t(lang, "menu_title"), reply_markup=main_menu_keyboard(lang))

    return router
