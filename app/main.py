import importlib
import logging

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .db import init_db
from .deps import LoginRequired
from .templating import templates

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="名刺管理", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware, secret_key=config.SECRET_KEY, same_site="lax", https_only=False, max_age=None
)
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "app" / "static")), name="static")


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.on_event("startup")
def _startup():
    key = config.SECRET_KEY
    if len(key) < 16 or key.startswith("change-me") or key == "dev-insecure-secret":
        raise RuntimeError(".env の SECRET_KEY が未設定、または推測可能な値です。16文字以上のランダム文字列を設定してください。")
    init_db()
    seed = importlib.import_module("app.routers.auth")
    seed.seed_initial_admin()


for _name in ("auth", "admin_users", "register", "cards", "grants"):
    _mod = importlib.import_module(f"app.routers.{_name}")
    app.include_router(_mod.router)


@app.get("/")
def index(request: Request):
    return RedirectResponse("/cards" if request.session.get("user_id") else "/login", status_code=303)
