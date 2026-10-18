"""Domain errors. Services raise these; main.py turns them into JSON responses:

    {"code": "version_conflict", "detail": "...", "current": {...latest item...}}
"""


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, detail: str, *, code: str | None = None, current=None):
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code
        # On conflicts: the latest state of the item, so the client can show it.
        self.current = current


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"
