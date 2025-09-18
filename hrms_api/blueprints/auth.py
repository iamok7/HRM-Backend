from flask import Blueprint, request, jsonify
from hrms_api.models.user import User

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

@bp.post("/simple-login")
def simple_login():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    u = User.query.filter_by(email=email).first()
    if not u or not u.check_password(password):
        return jsonify({"message": "Invalid credentials"}), 401
    return jsonify({"message": "ok", "user": {"id": u.id, "email": u.email, "full_name": u.full_name}})
