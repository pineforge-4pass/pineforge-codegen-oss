import pathlib

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

MAX_BODY = 256_000

# VERSION file copied in by Dockerfile from repo root. Single source of truth.
_VERSION_FILE = pathlib.Path(__file__).parent / "VERSION"
VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "unknown"

app = FastAPI(title="pineforge-codegen", version=VERSION)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "version": VERSION}


@app.post("/transpile", response_class=PlainTextResponse)
async def do_transpile(req: Request) -> str:
    raw = await req.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(status_code=413, detail="script too large")
    try:
        src = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail=f"utf-8 decode: {e}") from e
    # transpile() is sync + CPU-bound; off-load to FastAPI's threadpool so the
    # event loop can keep accepting connections while it runs.
    from fastapi.concurrency import run_in_threadpool
    try:
        return await run_in_threadpool(transpile, src)
    except CompileError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
