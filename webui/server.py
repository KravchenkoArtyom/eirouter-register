"""Сборка приложения WebUI: статика + роутеры API."""
from __future__ import annotations

import argparse

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from webui.api import accounts, inspector, mail, proxies, runs, scenarios, settings
from webui.settings import STATIC_DIR

app = FastAPI(title="Universal Autoregister WebUI")
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
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
