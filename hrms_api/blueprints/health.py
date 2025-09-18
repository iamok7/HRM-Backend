from flask import Blueprint, jsonify
bp = Blueprint("health", __name__, url_prefix="/api")

@bp.get("/healthz")
def healthz():
    return jsonify({"ok": True})
