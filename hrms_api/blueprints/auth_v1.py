from flask import Blueprint, request, jsonify
from flask_jwt_extended import (
    create_access_token, create_refresh_token,
    jwt_required, get_jwt_identity
)
from hrms_api.models.user import User
from hrms_api.extensions import db

bp = Blueprint("auth_v1", __name__, url_prefix="/api/v1/auth")

def _user_payload(u: User):
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "roles": u.role_codes()}

@bp.post("/login")
def login():
    data = request.get_json(silent=True, force=True)
    if not isinstance(data, dict):
        data = {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    u = User.query.filter_by(email=email).first()
    if not u or not u.check_password(password):
        return jsonify({"success": False, "error": {"message": "Invalid credentials"}}), 401

    roles = u.role_codes()
    add_claims = {"roles": roles, "email": u.email, "name": u.full_name}

    from datetime import timedelta
    expires = timedelta(days=1)
    access  = create_access_token(identity=str(u.id), additional_claims=add_claims, expires_delta=expires)
    refresh = create_refresh_token(identity=str(u.id), additional_claims={"roles": roles})
    return jsonify({"success": True, "access": access, "refresh": refresh, "user": _user_payload(u)}), 200

@bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    from flask_jwt_extended import get_jwt_identity
    uid = get_jwt_identity()              # will be string now
    u = User.query.get(int(uid)) if uid else None
    roles = u.role_codes() if u else []
    add_claims = {"roles": roles, "email": u.email if u else "", "name": u.full_name if u else ""}
    new_access = create_access_token(identity=str(uid), additional_claims=add_claims)
    return jsonify({"success": True, "access": new_access}), 200

@bp.get("/me")
@jwt_required()
def me():
    uid = get_jwt_identity()
    u = User.query.get(uid)
    if not u:
        return jsonify({"success": False, "error": {"message": "User not found"}}), 404
    return jsonify({"success": True, "data": _user_payload(u)}), 200
