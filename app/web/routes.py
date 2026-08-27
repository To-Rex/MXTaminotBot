import csv
import io
import json
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database import async_session
from app.services import api_log
from app.models import Bot, User
from app.services.auth_api import AuthAPIService

router = APIRouter(prefix="/panel")


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return "User-agent: *\nDisallow: /\n"


@router.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


def _render(request: Request, template_name: str, context: dict | None = None) -> HTMLResponse:
    env = request.app.state.jinja_env
    ctx = {
        "request": request,
        "username": request.session.get("username", ""),
    }
    if context:
        ctx.update(context)
    template = env.get_template(template_name)
    return HTMLResponse(template.render(ctx))


def _bot_manager(request: Request):
    return request.app.state.bot_manager


async def _get_bot_or_404(bot_id: int):
    async with async_session() as session:
        result = await session.execute(select(Bot).where(Bot.id == bot_id))
        bot = result.scalar_one_or_none()
        if bot is None:
            raise HTTPException(status_code=404, detail="Bot topilmadi")
        return bot


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    async with async_session() as session:
        result = await session.execute(select(Bot).order_by(Bot.created_at.desc()))
        bots = result.scalars().all()

        count_rows = await session.execute(
            select(User.bot_id, func.count(User.id)).group_by(User.bot_id)
        )
        counts_by_bot = dict(count_rows.all())
        user_counts = {bot.id: counts_by_bot.get(bot.id, 0) for bot in bots}

    bm = _bot_manager(request)
    return _render(request, "index.html", {
        "bots": bots,
        "user_counts": user_counts,
        "bot_manager": bm,
        "flash": request.query_params.get("msg", ""),
        "flash_kind": request.query_params.get("kind", "ok"),
    })


@router.get("/bots/create", response_class=HTMLResponse)
async def create_form(request: Request):
    return _render(request, "create.html")


@router.post("/bots/create")
async def create_bot(
    request: Request,
    name: str = Form(...),
    token: str = Form(...),
    company_name: str = Form(""),
    base_url: str = Form(""),
    one_c_login: str = Form(""),
    one_c_password: str = Form(""),
    is_active: bool = Form(False),
):
    token = token.strip()
    form_values = {
        "name": name, "token": token, "company_name": company_name,
        "base_url": base_url, "one_c_login": one_c_login,
        "one_c_password": one_c_password, "is_active": is_active,
    }

    async with async_session() as session:
        existing = await session.execute(select(Bot).where(Bot.token == token))
        if existing.scalar_one_or_none() is not None:
            return _render(request, "create.html", {
                "error": "Bu token bilan bot allaqachon mavjud.",
                "form": form_values,
            })

        bot = Bot(
            name=name,
            token=token,
            company_name=company_name,
            base_url=base_url,
            one_c_login=one_c_login,
            one_c_password=one_c_password,
            is_active=is_active,
        )
        session.add(bot)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return _render(request, "create.html", {
                "error": "Bu token bilan bot allaqachon mavjud.",
                "form": form_values,
            })
        await session.refresh(bot)

        if bot.is_active:
            await _bot_manager(request).add_bot(bot)

    return RedirectResponse(url="/panel", status_code=303)


@router.get("/bots/{bot_id}/edit", response_class=HTMLResponse)
async def edit_form(request: Request, bot_id: int):
    bot = await _get_bot_or_404(bot_id)
    return _render(request, "edit.html", {"bot": bot})


@router.post("/bots/{bot_id}/edit")
async def edit_bot(
    request: Request,
    bot_id: int,
    name: str = Form(...),
    token: str = Form(...),
    company_name: str = Form(""),
    base_url: str = Form(""),
    one_c_login: str = Form(""),
    one_c_password: str = Form(""),
    is_active: bool = Form(False),
):
    token = token.strip()
    bot = await _get_bot_or_404(bot_id)
    bm = _bot_manager(request)
    token_changed = bot.token != token

    if token_changed:
        async with async_session() as session:
            existing = await session.execute(
                select(Bot).where(Bot.token == token, Bot.id != bot_id)
            )
            if existing.scalar_one_or_none() is not None:
                return _render(request, "edit.html", {
                    "bot": bot,
                    "error": "Bu token bilan boshqa bot allaqachon mavjud.",
                })

    async with async_session() as session:
        bot = await session.merge(bot)
        bot.name = name
        bot.token = token
        bot.company_name = company_name
        bot.base_url = base_url
        bot.one_c_login = one_c_login
        bot.one_c_password = one_c_password
        bot.is_active = is_active
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            bot = await _get_bot_or_404(bot_id)
            return _render(request, "edit.html", {
                "bot": bot,
                "error": "Bu token bilan boshqa bot allaqachon mavjud.",
            })
        await session.refresh(bot)

        if token_changed or not bm.is_running(bot.id):
            if bot.is_active:
                await bm.restart_bot(bot)
            else:
                await bm.stop_bot(bot.id)
        elif not bot.is_active:
            await bm.stop_bot(bot.id)
        elif bot.is_active and not bm.is_running(bot.id):
            await bm.add_bot(bot)

    return RedirectResponse(url="/panel", status_code=303)


@router.post("/bots/{bot_id}/toggle")
async def toggle_bot(request: Request, bot_id: int):
    bot = await _get_bot_or_404(bot_id)
    bm = _bot_manager(request)

    async with async_session() as session:
        bot = await session.merge(bot)
        bot.is_active = not bot.is_active
        await session.commit()

    if bot.is_active:
        await bm.add_bot(bot)
    else:
        await bm.stop_bot(bot.id)

    return RedirectResponse(url="/panel", status_code=303)


@router.post("/bots/{bot_id}/delete")
async def delete_bot(request: Request, bot_id: int):
    bot = await _get_bot_or_404(bot_id)
    bm = _bot_manager(request)
    await bm.stop_bot(bot.id)

    async with async_session() as session:
        bot = await session.merge(bot)
        await session.delete(bot)
        await session.commit()

    return RedirectResponse(url="/panel", status_code=303)


@router.get("/bots/{bot_id}/stats", response_class=HTMLResponse)
async def bot_stats(request: Request, bot_id: int):
    bot = await _get_bot_or_404(bot_id)

    async with async_session() as session:
        total = await session.execute(
            select(func.count(User.id)).where(User.bot_id == bot_id)
        )
        total_users = total.scalar()

        recent_result = await session.execute(
            select(User)
            .where(User.bot_id == bot_id)
            .order_by(User.created_at.desc())
            .limit(50)
        )
        recent_users = recent_result.scalars().all()

    bm = _bot_manager(request)
    return _render(request, "stats.html", {
        "bot": bot,
        "total_users": total_users,
        "recent_users": recent_users,
        "is_running": bm.is_running(bot_id),
    })


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    token = request.session.get("access_token", "")
    profile = await AuthAPIService.get_profile(token) if token else None
    return _render(request, "profile.html", {"profile": profile or {}})


# ── 1C API logs (debug view) ───────────────────────────────────────────────
@router.get("/api-logs", response_class=HTMLResponse)
async def api_logs_page(request: Request):
    return _render(request, "api_logs.html", {"max_entries": api_log.MAX_ENTRIES})


@router.get("/api-logs/data")
async def api_logs_data():
    return {
        "entries": api_log.entries(),
        "endpoints": api_log.known_endpoints(),
        "stats": api_log.stats(),
    }


@router.post("/api-logs/clear")
async def api_logs_clear():
    api_log.clear()
    return {"success": True}


# ── Import / Export (botlar + ro'yxatdan o'tgan taminotchilar) ──────────────
EXPORT_FORMAT = "mx-taminot-bot/bots"
EXPORT_VERSION = 1


async def _collect_export() -> dict:
    """Barcha botlar va ularga bog'langan foydalanuvchilar."""
    async with async_session() as session:
        bots = (await session.execute(select(Bot).order_by(Bot.id))).scalars().all()
        users = (await session.execute(select(User).order_by(User.id))).scalars().all()
    by_bot: dict[int, list] = {}
    for u in users:
        by_bot.setdefault(u.bot_id, []).append({
            "telegram_id": u.telegram_id,
            "phone_number": u.phone_number,
            "client_id": u.client_id,
            "language": u.language,
        })
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "bots": [{
            "name": b.name,
            "token": b.token,
            "company_name": b.company_name or "",
            "base_url": b.base_url or "",
            "one_c_login": b.one_c_login or "",
            "one_c_password": b.one_c_password or "",
            "is_active": bool(b.is_active),
            "users": by_bot.get(b.id, []),
        } for b in bots],
    }


@router.get("/export")
async def export_json():
    """To'liq zaxira (JSON) — keyin shu fayl orqali qayta tiklash mumkin."""
    data = await _collect_export()
    body = json.dumps(data, ensure_ascii=False, indent=2)
    name = f"mx-taminot-bots-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    return Response(
        content=body, media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/export.csv")
async def export_csv():
    """Excel'da ko'rish uchun (import uchun emas — parollar va foydalanuvchilar yo'q)."""
    data = await _collect_export()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "company_name", "base_url", "one_c_login", "is_active", "users"])
    for b in data["bots"]:
        w.writerow([b["name"], b["company_name"], b["base_url"], b["one_c_login"],
                    "1" if b["is_active"] else "0", len(b["users"])])
    name = f"mx-taminot-bots-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
    return Response(
        content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/import")
async def import_json(request: Request, file: UploadFile = File(...)):
    """Zaxiradan tiklash / ko'chirish. Botlar `token` bo'yicha solishtiriladi:
    mavjud bo'lsa yangilanadi, bo'lmasa yaratiladi. Foydalanuvchilar
    (telegram_id) ham shu tarzda qo'shiladi — ular qayta ro'yxatdan o'tmaydi."""
    raw = await file.read()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return RedirectResponse(url="/panel?kind=err&msg=Fayl+JSON+emas", status_code=303)

    bots_in = data.get("bots") if isinstance(data, dict) else (data if isinstance(data, list) else None)
    if not isinstance(bots_in, list) or not bots_in:
        return RedirectResponse(url="/panel?kind=err&msg=Faylda+bot+ma%27lumoti+topilmadi", status_code=303)

    created = updated = users_added = skipped = 0
    touched: list[int] = []
    async with async_session() as session:
        for item in bots_in:
            if not isinstance(item, dict) or not str(item.get("token") or "").strip():
                skipped += 1
                continue
            token = str(item["token"]).strip()
            bot = (await session.execute(select(Bot).where(Bot.token == token))).scalar_one_or_none()
            if bot is None:
                bot = Bot(token=token)
                session.add(bot)
                created += 1
            else:
                updated += 1
            bot.name = str(item.get("name") or bot.name or token[:10])
            bot.company_name = str(item.get("company_name") or "")
            bot.base_url = str(item.get("base_url") or "")
            bot.one_c_login = str(item.get("one_c_login") or "")
            bot.one_c_password = str(item.get("one_c_password") or "")
            bot.is_active = bool(item.get("is_active", False))
            await session.flush()

            for u in item.get("users") or []:
                if not isinstance(u, dict) or not u.get("telegram_id"):
                    continue
                tg = int(u["telegram_id"])
                user = (await session.execute(
                    select(User).where(User.telegram_id == tg, User.bot_id == bot.id)
                )).scalar_one_or_none()
                if user is None:
                    user = User(telegram_id=tg, bot_id=bot.id)
                    session.add(user)
                    users_added += 1
                user.phone_number = u.get("phone_number")
                user.client_id = u.get("client_id")
                lang = str(u.get("language") or "uz")
                user.language = lang if lang in ("uz", "ru") else "uz"
            touched.append(bot.id)
        await session.commit()

    # botlarni yangi sozlama bilan qayta ishga tushiramiz
    bm = _bot_manager(request)
    async with async_session() as session:
        for bot_id in touched:
            bot = (await session.execute(select(Bot).where(Bot.id == bot_id))).scalar_one_or_none()
            if bot is None:
                continue
            try:
                if bot.is_active:
                    await bm.restart_bot(bot)
                else:
                    await bm.stop_bot(bot.id)
            except Exception:  # noto'g'ri token va h.k. — import baribir saqlanadi
                pass

    msg = f"Import: {created} ta yangi bot, {updated} ta yangilandi, {users_added} ta foydalanuvchi"
    if skipped:
        msg += f", {skipped} ta o'tkazib yuborildi"
    return RedirectResponse(url=f"/panel?kind=ok&msg={msg.replace(' ', '+')}", status_code=303)
