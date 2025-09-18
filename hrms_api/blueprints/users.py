from flask import Blueprint, jsonify
from hrms_api.models.user import User

bp = Blueprint("users", __name__, url_prefix="/api/users")

@bp.get("")
def list_users():
    rows = User.query.order_by(User.id.asc()).all()
    return jsonify([
        {"id": u.id, "email": u.email, "full_name": u.full_name, "status": u.status}
        for u in rows
    ])
