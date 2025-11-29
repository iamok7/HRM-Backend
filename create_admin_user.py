from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.user import User
from hrms_api.models.security import Role, UserRole

app = create_app()

with app.app_context():
    email = "admin@hrms.local"
    password = "password"
    
    u = User.query.filter_by(email=email).first()
    if not u:
        print(f"Creating user {email}...")
        u = User(email=email, full_name="Admin User", status="active")
        u.set_password(password)
        db.session.add(u)
        db.session.flush()
    else:
        print(f"User {email} already exists. Resetting password...")
        u.set_password(password)
    
    # Assign admin role
    admin_role = Role.query.filter_by(code="admin").first()
    if admin_role:
        has_role = UserRole.query.filter_by(user_id=u.id, role_id=admin_role.id).first()
        if not has_role:
            print("Assigning admin role...")
            db.session.add(UserRole(user_id=u.id, role_id=admin_role.id))
    
    db.session.commit()
    print("Admin user ready.")
