"""Сборка приложения WebUI: статика, роутеры API, защита и корректный выход."""
from __future__ import annotations

import argparse
import threading
import webbrowser
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from webui import security
from webui.api import accounts, inspector, mail, proxies, runs, scenarios, settings
from webui.settings import STATIC_DIR


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Выход из панели закрывает браузеры инспектора и писарей аккаунтов."""
    yield
    inspector.shutdown()
    runs.shutdown()


app = FastAPI(title="Universal Autoregister WebUI", lifespan=lifespan)
security.install(app)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(accounts.router)
app.include_router(scenarios.router)
app.include_router(mail.router)
app.include_router(proxies.router)
app.include_router(settings.router)
app.include_router(runs.router)
app.include_router(inspector.router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Universal Autoregister WebUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-token", action="store_true",
                        help="не требовать токен (только для отладки)")
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    args = parser.parse_args()
    token = "" if args.no_token else security.new_token()
    security.configure(token=token, hosts=security.allowed_hosts(args.host))
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    address = f"http://{shown}:{args.port}/"
    link = f"{address}?{security.TOKEN_PARAM}={token}" if token else address
    print(f"Панель: {link}", flush=True)
    if token:
        print("Токен нужен только при первом открытии: дальше он живёт в cookie.",
              flush=True)
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(link,)).start()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
