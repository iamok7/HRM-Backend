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
