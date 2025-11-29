from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from .http import fail

def register_error_handlers(app):
    @app.errorhandler(HTTPException)
    def _http(e: HTTPException):
        return fail(e.description or e.name, status=e.code or 500)

    @app.errorhandler(IntegrityError)
    def _dup(e: IntegrityError):
        return fail("Duplicate or FK constraint failed", status=409, code="CONSTRAINT_ERROR")

    @app.errorhandler(404)
    def _404(_): return fail("Not found", status=404)

    @app.errorhandler(Exception)
    def _500(e: Exception):
        app.logger.exception(e)
        return fail("Internal server error", status=500)


# hrms_api/common/errors.py
from flask import Blueprint
from werkzeug.exceptions import HTTPException
from sqlalchemy.exc import IntegrityError
from hrms_api.common.http import fail

bp_errors = Blueprint("errors", __name__)

@bp_errors.app_errorhandler(HTTPException)
def _http(e: HTTPException):
    return fail(message=e.description or "HTTP error", status=e.code or 400)

@bp_errors.app_errorhandler(IntegrityError)
def _integrity(e: IntegrityError):
    # 409 for unique/FK violations
    return fail(message="Conflict / integrity error", status=409, detail=str(e.orig) if getattr(e, "orig", None) else str(e))

@bp_errors.app_errorhandler(Exception)
def _unhandled(e: Exception):
    return fail(message="Internal Server Error", status=500, detail=str(e))

class APIError(Exception):
    """Custom API Error class."""
    def __init__(self, code, message, status_code=400, payload=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.payload = payload

@bp_errors.app_errorhandler(APIError)
def _api_error(e: APIError):
    return fail(message=e.message, status=e.status_code, code=e.code, detail=e.payload)
