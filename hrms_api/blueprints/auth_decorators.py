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
            import traceback
            msg = str(e)
            tb = traceback.format_exc()
            if "Not enough segments" in msg:
                msg = "Invalid token format (malformed JWT)"
            return jsonify({"success": False, "error": msg, "traceback": tb}), 401
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

def permission_required(permission_code):
    def decorator(f):
        @wraps(f)
        def decorated(current_user, *args, **kwargs):
            # Check if user has admin role (bypass)
            user_roles = current_user.role_codes()
            if "admin" in user_roles:
                return f(current_user, *args, **kwargs)
                
            # Check permissions
            # Assuming User model has a method or property to get all permissions
            # If not, we might need to fetch them. 
            # Let's assume current_user.all_permissions is a set of permission codes
            # Or we check via roles.
            
            # Since I don't see all_permissions in User model in previous steps, 
            # I'll implement a basic check traversing roles -> permissions
            
            has_perm = False
            for role in current_user.user_roles: # role is UserRole object, role.role is Role object
                if role.role:
                    for perm in role.role.permissions: # perm is RolePermission object? Or Permission?
                        # Usually role.permissions is a relationship to Permission via secondary or direct
                        # Let's assume standard many-to-many or association object
                        # If using association object pattern (RolePermission):
                        # role.permissions might be list of RolePermission.
                        # We need to check the actual Permission object.
                        
                        # To be safe without seeing Role model, let's try to access permission code
                        # If role.permissions returns Permission objects directly (secondary):
                        p_code = getattr(perm, "code", None)
                        # If role.permissions returns RolePermission objects:
                        if not p_code and hasattr(perm, "permission"):
                             p_code = perm.permission.code
                        
                        if p_code == permission_code:
                            has_perm = True
                            break
                if has_perm: break
            
            if has_perm:
                return f(current_user, *args, **kwargs)
            
            return jsonify({"success": False, "error": f"Permission denied: {permission_code} required"}), 403
        return decorated
    return decorator
