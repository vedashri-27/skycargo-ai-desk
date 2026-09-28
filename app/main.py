import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from chatkit.server import StreamingResult  # noqa: E402
from fastapi import FastAPI, HTTPException, Request, Response  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402

from .server import SkyCargoServer  # noqa: E402
from .store import RequestContext, SQLiteStore  # noqa: E402

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("skycargo")

ROOT = Path(__file__).resolve().parent.parent
store = SQLiteStore(os.getenv("CHAT_DB", ROOT / "chat.db"))
server = SkyCargoServer(store)

app = FastAPI(title="SkyCargo AI Support Desk")

USER_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def request_context(request: Request) -> RequestContext:
    """Identify the user.

    Demo only: the browser sends a random id it keeps in localStorage.
    In production, replace this with a verified session or JWT.
    """
    user_id = request.headers.get("x-user-id", "")
    if not USER_ID.match(user_id):
        raise HTTPException(status_code=401, detail="Missing or invalid x-user-id header")
    return RequestContext(user_id=user_id)


@app.post("/chatkit")
async def chatkit_endpoint(request: Request):
    context = request_context(request)
    result = await server.process(await request.body(), context)
    if isinstance(result, StreamingResult):
        return StreamingResponse(result, media_type="text/event-stream")
    return Response(content=result.json, media_type="application/json")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(ROOT / "frontend" / "index.html")
