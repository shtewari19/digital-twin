"""RFC 7807 `application/problem+json` error responses.

The API contract says every non-2xx response carries a `Problem` body
(see `app.schemas.common.Problem`), but FastAPI's defaults return
`{"detail": ...}` as `application/json`. The handlers registered by
`install_problem_handlers` translate the three error sources we actually
raise — `ProblemError` (deliberate, with a problem `type`), plain
`HTTPException`, and Pydantic request validation — into that envelope.

Routes should raise `ProblemError` (or one of the `conflict` /
`not_found` / `unprocessable` shortcuts) so the response carries a stable
machine-readable `type` URI the frontend can branch on, rather than
matching on prose.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

PROBLEM_CONTENT_TYPE = "application/problem+json"

# Stable problem `type` URIs. Kept as constants so a route and a test can
# refer to the same identifier.
BASE = "https://api.digitaltwin.local/problems"
TYPE_NOT_FOUND = f"{BASE}/not-found"
TYPE_CONFLICT = f"{BASE}/conflict"
TYPE_INVALID_STATE = f"{BASE}/invalid-run-state"
TYPE_ACTIVE_RUN = f"{BASE}/active-run-exists"
TYPE_VALIDATION = f"{BASE}/validation-error"
TYPE_RATE_LIMIT = f"{BASE}/rate-limit-exceeded"
TYPE_IDEMPOTENCY_CONFLICT = f"{BASE}/idempotency-key-reuse"


class ProblemError(Exception):
    """An error that should render as a specific RFC 7807 problem type."""

    def __init__(
        self,
        status: int,
        detail: str,
        *,
        type_: str = "about:blank",
        title: str | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.type = type_
        self.title = title or HTTPStatus(status).phrase
        self.errors = errors


def not_found(detail: str) -> ProblemError:
    return ProblemError(404, detail, type_=TYPE_NOT_FOUND)


def conflict(detail: str, *, type_: str = TYPE_CONFLICT) -> ProblemError:
    return ProblemError(409, detail, type_=type_)


def invalid_state(detail: str) -> ProblemError:
    """409 for a transition the run's current status doesn't allow."""
    return ProblemError(409, detail, type_=TYPE_INVALID_STATE)


def unprocessable(detail: str) -> ProblemError:
    return ProblemError(422, detail, type_=TYPE_VALIDATION)


def _body(
    *,
    status: int,
    detail: str,
    instance: str,
    type_: str = "about:blank",
    title: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": type_,
        "title": title or HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "instance": instance,
    }
    if errors:
        body["errors"] = errors
    return body


def install_problem_handlers(app: FastAPI) -> None:
    """Register the three handlers. Call once, from `app.main`."""

    @app.exception_handler(ProblemError)
    async def _problem(_request: Request, exc: ProblemError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            media_type=PROBLEM_CONTENT_TYPE,
            content=_body(
                status=exc.status,
                detail=exc.detail,
                instance=str(_request.url.path),
                type_=exc.type,
                title=exc.title,
                errors=exc.errors,
            ),
        )

    @app.exception_handler(HTTPException)
    async def _http(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            media_type=PROBLEM_CONTENT_TYPE,
            headers=exc.headers,
            content=_body(
                status=exc.status_code,
                detail=str(exc.detail),
                instance=str(_request.url.path),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            media_type=PROBLEM_CONTENT_TYPE,
            content=_body(
                status=422,
                detail="Validation failed.",
                instance=str(_request.url.path),
                type_=TYPE_VALIDATION,
                errors=[
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                    for e in exc.errors()
                ],
            ),
        )
