import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from starlette.middleware.sessions import SessionMiddleware

from app.config import HOST, LOG_LEVEL, PORT, SESSION_SECRET_KEY
from app.database import async_session, engine
from app.models import Base
from app.services.bot_manager import BotManager
from app.services.http_client import close_http_client
from app.services.supplier_api import ServiceUnavailable, SupplierError
from app.web.auth import AuthMiddleware, router as auth_router
from app.web.routes import router as web_router
from app.web.web_app_api import router as webapp_api_router

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all skips indexes on pre-existing tables — add them explicitly
        for table in Base.metadata.tables.values():
            for index in table.indexes:
                await conn.run_sync(lambda sync_conn, idx=index: idx.create(sync_conn, checkfirst=True))

    bot_manager = BotManager(async_session)
    app.state.bot_manager = bot_manager
    app.state.jinja_env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=True,
        auto_reload=False,  # templates are static in production — skip stat() per render
    )

    await bot_manager.start_all()
    logger.info("Application started")

    yield

    await bot_manager.stop_all()
    await close_http_client()
    await engine.dispose()
    logger.info("Application stopped")


app = FastAPI(title="Telegram Supplier Bot Manager", lifespan=lifespan)

app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

SERVICE_ERROR_MESSAGE = ("Маълумот олинмади: 1C билан алоқа йўқ ёки хизмат жавоб бермаяпти. "
                         "Бироздан сўнг қайта уриниб кўринг.")


@app.exception_handler(ServiceUnavailable)
async def _service_unavailable_handler(request: Request, exc: ServiceUnavailable):
    logger.warning("1C service unavailable: %s (%s)", exc.endpoint, exc.reason)
    return JSONResponse(status_code=503, content={"detail": SERVICE_ERROR_MESSAGE, "code": "SERVICE_UNAVAILABLE", "endpoint": exc.endpoint})


@app.exception_handler(SupplierError)
async def _supplier_error_handler(request: Request, exc: SupplierError):
    logger.warning("1C error %s: %s (%s)", exc.code, exc.message, exc.endpoint)
    return JSONResponse(status_code=400, content={"detail": exc.message, "code": exc.code})


app.include_router(auth_router)
app.include_router(web_router)
app.include_router(webapp_api_router)


@app.get("/webapp", response_class=HTMLResponse)
async def webapp_page(request: Request):
    env = request.app.state.jinja_env
    template = env.get_template("webapp.html")
    return HTMLResponse(template.render())


@app.get("/webapp/", response_class=HTMLResponse)
async def webapp_slash(request: Request):
    return await webapp_page(request)


def _port_owner(port: int) -> str:
    """Portni band qilgan jarayon tavsifi (topilmasa bo'sh satr)."""
    import subprocess
    try:
        pids = subprocess.run(
            ["lsof", "-t", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5,
        ).stdout.split()
        if not pids:
            return ""
        info = subprocess.run(
            ["ps", "-o", "pid=,command=", "-p", ",".join(pids)],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return info
    except Exception:  # lsof/ps yo'q yoki ruxsat yo'q — muhim emas
        return ""


def _port_is_busy(host: str, port: int) -> bool:
    """Portda kimdir tinglayaptimi.

    Bind bilan tekshirish macOS'da ishonchsiz (SO_REUSEADDR semantikasi tufayli
    band port ham bo'sh ko'rinishi mumkin), shuning uchun ulanib ko'ramiz:
    ulanish muvaffaqiyatli bo'lsa — port band.
    """
    import socket
    target = "127.0.0.1" if host in ("0.0.0.0", "", "::", "::0") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.7)
        try:
            return s.connect_ex((target, port)) == 0
        except OSError:
            return False


def main():
    import uvicorn

    # Portni OLDINDAN tekshiramiz: aks holda uvicorn bind'da yiqilib, allaqachon
    # ishga tushgan botlar to'xtatiladi va foydalanuvchi tushunarsiz aiogram
    # traceback'ini ko'radi (asl sabab — port band ekani — ko'rinmay qoladi).
    owner = _port_owner(PORT)
    if owner or _port_is_busy(HOST, PORT):
        logger.error(
            "%s:%s porti band — ilova ishga tushmadi.\n"
            "   Сабаби: одатда шу лойиҳанинг бошқа нусхаси аллақачон ишлаб турибди.\n"
            "%s"
            "   Ечим: (1) ишлаётганини ишлатинг — http://127.0.0.1:%s/panel\n"
            "         (2) ёки уни тўхтатинг:  kill <PID>   (жавоб бермаса: kill -9 <PID>)\n"
            "         (3) ёки .env да бошқа порт кўрсатинг: PORT=8001\n"
            "   Эслатма: битта бот токени билан икки нусха ишласа Telegram 409 Conflict беради.",
            HOST, PORT,
            f"   Портни банд қилган жараён:\n      {owner}\n" if owner else "",
            PORT,
        )
        raise SystemExit(1)

    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level=LOG_LEVEL.lower())


if __name__ == "__main__":
    main()
