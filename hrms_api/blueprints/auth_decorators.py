from functools import wraps
from flask import jsonify
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity, get_jwt
from hrms_api.models.user import User
from hrms_api.extensions import db

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            verify_jwt_in_request()
            uid = get_jwt_identity()
            user_id = int(uid) if uid else None
            if not user_id:
                return jsonify({"success": False, "error": "Invalid token"}), 401
            current_user = db.session.get(User, user_id)
            if not current_user:
                return jsonify({"success": False, "error": "User not found"}), 401
            return f(current_user, *args, **kwargs)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 401
    return decorated

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(current_user, *args, **kwargs):
            user_roles = current_user.role_codes()
            if "admin" in user_roles:
                return f(current_user, *args, **kwargs)
            
            for r in roles:
                if r in user_roles:
                    return f(current_user, *args, **kwargs)
            
            return jsonify({"success": False, "error": "Permission denied"}), 403
        return decorated
    return decorator
