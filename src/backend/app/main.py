from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status

from app.catalog import public_menu
from app.config import get_settings
from app.models import ChatRequest, ChatResponse, SessionResponse
from app.rate_limit import SessionRateLimiter
from app.sandbox_manager import SandboxError, SandboxManager
from app.telemetry import configure_telemetry

settings = get_settings()
logger = configure_telemetry(settings.log_level)
manager = SandboxManager(settings, logger)
rate_limiter = SessionRateLimiter(settings.requests_per_minute)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await manager.start()
    try:
        yield
    finally:
        await manager.stop()


app = FastAPI(
    title="McDonald's Copilot Sandbox API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": settings.copilot_model}


@app.get("/ready")
async def ready() -> dict:
    try:
        settings.require_runtime_secrets()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return {"status": "ready"}


@app.get("/api/menu")
async def menu() -> list[dict]:
    return public_menu()


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    if not await rate_limiter.allow(request.session_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁，请稍后再试。",
        )
    try:
        reply, sandbox_id = await manager.chat(
            session_id=request.session_id,
            message=request.message,
            cart=[item.model_dump() for item in request.cart],
            locale=request.locale,
        )
    except SandboxError as exc:
        logger.error("chat_failed", extra={"session_id": request.session_id})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Kata 点餐助手暂时无法回复，请稍后重试。",
        ) from exc
    return ChatResponse(
        session_id=request.session_id,
        message=reply,
        sandbox_id=sandbox_id,
        model=settings.copilot_model,
    )


@app.delete("/api/sessions/{session_id}", response_model=SessionResponse)
async def delete_session(session_id: str) -> SessionResponse:
    try:
        deleted = await manager.delete(session_id)
    except SandboxError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="无法清理 Kata 会话。",
        ) from exc
    await rate_limiter.remove(session_id)
    return SessionResponse(session_id=session_id, deleted=deleted)
