# from flask import jsonify

# def ok(data=None, status=200, **meta):
#     payload = {"success": True, "data": data}
#     if meta: payload["meta"] = meta
#     return jsonify(payload), status

# def fail(message, status=400, code=None, details=None):
#     err = {"message": message}
#     if code: err["code"] = code
#     if details: err["details"] = details
#     return jsonify({"success": False, "error": err}), status
# hrms_api/common/http.py
from flask import jsonify

def ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status

def fail(message="Bad Request", status=400, code=None, detail=None, errors=None):
    err = {"message": message}
    if code: err["code"] = code
    if detail: err["detail"] = detail
    if errors: err["errors"] = errors
    return jsonify({"success": False, "error": err}), status
