"""Global exception handlers that convert typed errors into JSON responses."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .app_error import AppError


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
        headers=exc.headers,
    )


def _scrub_validation_errors(errors: list) -> list:
    """Drop the `input` echo from a 422 body.

    FastAPI's default handler replays the value that failed validation. On a
    field-level failure that value is the field; on a `missing` failure it is the
    WHOLE submitted body. Measured live 2026-08-09 against
    `POST /api/auth/register` with a bad email: the response carried
    `"input": {"email": ..., "password": "<the plaintext password>"}` — an
    unauthenticated request handing the submitted password back over the wire,
    into every proxy access log, browser cache and error-reporting tool that
    stores response bodies.

    ★`loc` and `msg` are kept, and that is the point of scrubbing rather than
    replacing: the client still learns exactly WHICH field is wrong and WHY,
    so the form can still highlight it. Only the value is withheld, and the
    client already has the value.

    ★`ctx` is dropped too. Pydantic puts constraint context there, but some
    validators put the offending value in it as well, so keeping it would leave
    a second copy of the thing this function exists to remove.
    """
    scrubbed = []
    for err in errors:
        if not isinstance(err, dict):
            scrubbed.append(err)
            continue
        clean = {k: v for k, v in err.items() if k not in ("input", "ctx", "url")}
        scrubbed.append(clean)
    return scrubbed


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": _scrub_validation_errors(list(exc.errors()))},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire AppError handling into the FastAPI app.

    Kept as a function so tests and alternative app factories can share the
    same registration without duplicating decorator wiring in main.py.
    """
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
