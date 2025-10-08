import os
import click
from flask import Flask
from flask_cors import CORS

from hrms_api.extensions import db
from hrms_api.common.errors import register_error_handlers
from hrms_api.models import load_all


from datetime import timedelta, datetime
from flask_jwt_extended import JWTManager


import click
from flask.cli import with_appcontext

jwt = JWTManager()

def create_app():
    app = Flask(__name__)

    # Config
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret")
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(minutes=30)
    app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(days=7)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:4445@127.0.0.1:5432/hrms_dev",
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # CORS (dev)
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Extensions
    db.init_app(app)
    register_error_handlers(app)
    jwt.init_app(app)

    # Ensure models are loaded so metadata is complete
    with app.app_context():
        load_all()

    # Blueprints
    from hrms_api.blueprints.health import bp as health_bp
    from hrms_api.blueprints.auth import bp as auth_bp
    from hrms_api.blueprints.users import bp as users_bp
    from hrms_api.blueprints.master_companies import bp as companies_bp
    from hrms_api.blueprints.master_locations import bp as locations_bp
    from hrms_api.blueprints.master_departments import bp as departments_bp
    from hrms_api.blueprints.master_designations import bp as designations_bp
    from hrms_api.blueprints.master_grades import bp as grades_bp
    from hrms_api.blueprints.master_cost_centers import bp as cost_centers_bp
    from hrms_api.blueprints.auth_v1 import bp as auth_v1_bp
    from hrms_api.blueprints.employees import bp as employees_bp
    from hrms_api.blueprints.employee_extras import bp as employee_extras_bp
    from hrms_api.blueprints.attendance_masters import bp as attendance_bp
    from hrms_api.blueprints.attendance_assignments import bp as attendance_assignments_bp
    from hrms_api.blueprints.attendance_calendar import bp as attendance_calendar_bp
    from hrms_api.blueprints.attendance_punches import bp as attendance_punches_bp
    from hrms_api.blueprints.attendance_monthly import bp as attendance_monthly_bp
    from hrms_api.blueprints.attendance_self_punch import bp as attendance_self_punch_bp
    from hrms_api.blueprints.attendance_punch_import import bp as attendance_punch_import_bp
    from hrms_api.blueprints.leave import bp as leave_bp
    from hrms_api.blueprints.attendance_missed_punch import bp as attendance_missed_bp
    from hrms_api.blueprints.security import bp as security_bp
    from .rbac import bp as rbac_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(companies_bp)
    app.register_blueprint(locations_bp)
    app.register_blueprint(departments_bp)
    app.register_blueprint(designations_bp)
    app.register_blueprint(grades_bp)
    app.register_blueprint(cost_centers_bp)
    app.register_blueprint(auth_v1_bp)
    app.register_blueprint(employees_bp)
    app.register_blueprint(employee_extras_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(attendance_assignments_bp)
    app.register_blueprint(attendance_calendar_bp)
    app.register_blueprint(attendance_punches_bp)
    app.register_blueprint(attendance_monthly_bp)
    app.register_blueprint(attendance_self_punch_bp)
    app.register_blueprint(attendance_punch_import_bp)
    app.register_blueprint(leave_bp)
    app.register_blueprint(attendance_missed_bp)
    app.register_blueprint(security_bp)
    app.register_blueprint(rbac_bp)

    
    # in hrms_api/__init__.py (inside create_app, after app.register_blueprint(rbac_bp))
    with app.app_context():
        try:
            from hrms_api.rbac import _ensure_auth_settings, _apply_settings_to_app
            s = _ensure_auth_settings()
            _apply_settings_to_app(s)
        except Exception as e:
            app.logger.warning("Could not warm auth settings: %s", e)



    # ---- CLI (registered on this app instance) ----
    @app.cli.command("seed-core")
    def seed_core():
        """Seed demo company and core users (admin, HR, employee)."""
        from hrms_api.models.user import User
        from hrms_api.models.master import Company
        from hrms_api.models.security import Role, UserRole
        from hrms_api.models.employee import Employee

        def ensure_role(code: str) -> Role:
            role = Role.query.filter_by(code=code).first()
            if not role:
                role = Role(code=code)
                db.session.add(role)
                db.session.commit()
            return role

        def ensure_user(email: str, full_name: str, role_code: str, password: str = "4445"):
            user = User.query.filter_by(email=email).first()
            created = False
            if not user:
                user = User(email=email, full_name=full_name, status="active")
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                created = True

            role = ensure_role(role_code)
            if not any(ur.role_id == role.id for ur in user.user_roles):
                db.session.add(UserRole(user_id=user.id, role_id=role.id))
                db.session.commit()

            return user, created

        def ensure_employee_profile(email: str, first_name: str, last_name: str = "Demo"):
            emp = Employee.query.filter_by(email=email).first()
            if emp:
                return False

            c, loc, dept, des, grd, cc = _ensure_demo_masters()
            emp = Employee(
                company_id=c.id,
                location_id=loc.id if loc else None,
                department_id=dept.id if dept else None,
                designation_id=des.id if des else None,
                grade_id=grd.id if grd else None,
                cost_center_id=cc.id if cc else None,
                code="EMP-DEMO",
                email=email,
                first_name=first_name,
                last_name=last_name,
                employment_type="fulltime",
                status="active",
                doj=datetime.utcnow().date(),
            )
            db.session.add(emp)
            db.session.commit()
            return True

        c = Company.query.filter_by(code="DEMO").first()
        if not c:
            c = Company(code="DEMO", name="Demo Co")
            db.session.add(c)
            db.session.commit()

        admin_user, admin_created = ensure_user("admin@demo.local", "Demo Admin", "admin")
        hr_user, hr_created = ensure_user("hr@swstk.in", "HR Manager", "hr")
        employee_user, employee_created = ensure_user("emp@swstk.in", "Demo Employee", "employee")
        employee_profile_created = ensure_employee_profile(employee_user.email, "Demo", "Employee")

        click.echo(
            "Seeded/ensured: company DEMO; "
            f"admin@demo.local ({'created' if admin_created else 'existing'}) / 4445; "
            f"hr@swstk.in ({'created' if hr_created else 'existing'}) / 4445; "
            f"emp@swstk.in ({'created' if employee_created else 'existing'}) / 4445"
            + ("; employee profile created" if employee_profile_created else "")
        )

    @app.cli.command("seed-masters")
    def seed_masters():
        """Seed sample locations under DEMO company."""
        from hrms_api.models.master import Company, Location
        c = Company.query.filter_by(code="DEMO").first()
        if not c:
            click.echo("Run flask seed-core first (creates DEMO company).")
            return
        for nm in ("Pune", "Mumbai"):
            if not c.locations.filter_by(name=nm).first():
                db.session.add(Location(company_id=c.id, name=nm))
        db.session.commit()
        click.echo("Seeded locations: Pune, Mumbai")

    @app.cli.command("seed-more-masters")
    def seed_more_masters():
        from hrms_api.models.master import Company, Department, Designation, Grade, CostCenter
        c = Company.query.filter_by(code="DEMO").first()
        if not c:
            click.echo("Run flask seed-core first."); return
        # Departments
        d_eng = c.departments.filter_by(name="Engineering").first()
        if not d_eng:
            d_eng = Department(company_id=c.id, name="Engineering"); db.session.add(d_eng)
        d_hr = c.departments.filter_by(name="HR").first()
        if not d_hr:
            d_hr = Department(company_id=c.id, name="HR"); db.session.add(d_hr)
        db.session.commit()
        # Designations
        if not d_eng.designations.filter_by(name="Software Engineer").first():
            db.session.add(Designation(department_id=d_eng.id, name="Software Engineer"))
        if not d_hr.designations.filter_by(name="HR Executive").first():
            db.session.add(Designation(department_id=d_hr.id, name="HR Executive"))
        # Grades
        for g in ("G1","G2","G3"):
            if not Grade.query.filter_by(name=g).first():
                db.session.add(Grade(name=g))
        # Cost centers
        for code, name in (("CC-001","Corporate"),("CC-ENG","Engineering"),("CC-HR","Human Resources")):
            if not CostCenter.query.filter_by(code=code).first():
                db.session.add(CostCenter(code=code, name=name))
        db.session.commit()
        click.echo("Seeded: departments, designations, grades, cost centers.")

    @app.cli.command("seed-auth")
    def seed_auth():
        """Create 'admin' role and assign to admin@demo.local user."""
        from hrms_api.models.security import Role, UserRole
        from hrms_api.models.user import User
        admin = Role.query.filter_by(code="admin").first()
        if not admin:
            admin = Role(code="admin"); db.session.add(admin); db.session.commit()
        u = User.query.filter_by(email="admin@demo.local").first()
        if not u:
            click.echo("User admin@demo.local not found. Run flask seed-core first."); return
        link = UserRole.query.filter_by(user_id=u.id, role_id=admin.id).first()
        if not link:
            db.session.add(UserRole(user_id=u.id, role_id=admin.id)); db.session.commit()
        click.echo("Seeded role 'admin' and linked to admin@demo.local")

        @app.cli.command("seed-employees-10")
        def seed_employees_10():
            """
            Create/ensure employees with IDs 1..10 under DEMO company so CSVs using employee_id 1..10 work.
            Safe to run multiple times. If the employees table is empty or has <10, this will upsert IDs 1..10.
            """
            from sqlalchemy import text
            from hrms_api.models.master import Company, Location, Department, Designation, Grade, CostCenter
            from hrms_api.models.employee import Employee

            c = Company.query.filter_by(code="DEMO").first()
            if not c:
                click.echo("Company DEMO missing. Run: flask seed-core")
                return

            # Ensure baseline masters exist
            loc = Location.query.filter_by(company_id=c.id, name="Pune").first()
            if not loc:
                loc = Location(company_id=c.id, name="Pune"); db.session.add(loc); db.session.commit()
            dept = Department.query.filter_by(company_id=c.id, name="Engineering").first()
            if not dept:
                dept = Department(company_id=c.id, name="Engineering"); db.session.add(dept); db.session.commit()
            des = Designation.query.filter_by(department_id=dept.id, name="Software Engineer").first()
            if not des:
                des = Designation(department_id=dept.id, name="Software Engineer"); db.session.add(des); db.session.commit()
            grd = Grade.query.filter_by(name="G1").first()
            if not grd:
                grd = Grade(name="G1"); db.session.add(grd); db.session.commit()
            cc = CostCenter.query.filter_by(code="CC-ENG").first()
            if not cc:
                cc = CostCenter(code="CC-ENG", name="Engineering"); db.session.add(cc); db.session.commit()

            created = 0
            updated = 0
            for i in range(1, 11):
                email = f"emp{i}@demo.local"
                code = f"E-{i:04d}"

                emp = Employee.query.get(i)  # we try to pin specific IDs 1..10
                if not emp:
                    # Create with explicit ID so your CSV employee_id lines up
                    emp = Employee(
                        id=i,
                        company_id=c.id,
                        location_id=loc.id,
                        department_id=dept.id,
                        designation_id=des.id,
                        grade_id=grd.id,
                        cost_center_id=cc.id,
                        code=code,
                        email=email,
                        first_name=f"Emp{i}",
                        last_name="Demo",
                        doj=datetime.utcnow().date(),
                        employment_type="fulltime",
                        status="active",
                    )
                    db.session.add(emp)
                    created += 1
                else:
                    # Keep existing but ensure a few useful fields are filled
                    changed = False
                    if not getattr(emp, "email", None):
                        emp.email = email; changed = True
                    if not getattr(emp, "code", None):
                        emp.code = code; changed = True
                    if not getattr(emp, "status", None):
                        emp.status = "active"; changed = True
                    if changed: updated += 1

            db.session.commit()

            # Reset the sequence for Postgres if we manually set IDs
            try:
                db.session.execute(text(
                    "SELECT setval(pg_get_serial_sequence('employees','id'), (SELECT MAX(id) FROM employees))"
                ))
                db.session.commit()
            except Exception:
                # Not Postgres or no serial sequence — ignore
                pass

            click.echo(f"Employees ensured: created={created}, updated={updated}. IDs 1..10 are present.")

        @app.cli.command("seed-users-10")
        def seed_users_10():
            """
            Create/ensure user accounts emp1..emp10 (password=4445) for quick logins.
            """
            from hrms_api.models.user import User

            created = 0
            updated = 0
            for i in range(1, 11):
                email = f"emp{i}@demo.local"
                full_name = f"Employee {i}"
                u = User.query.filter_by(email=email).first()
                if not u:
                    u = User(email=email, full_name=full_name, status="active")
                    u.set_password("4445")
                    db.session.add(u)
                    created += 1
                else:
                    # keep status active and name current
                    changed = False
                    if getattr(u, "full_name", "") != full_name:
                        u.full_name = full_name; changed = True
                    if getattr(u, "status", "active") != "active":
                        u.status = "active"; changed = True
                    if changed: updated += 1

            db.session.commit()
            click.echo(f"Users ensured (emp1..emp10), password=4445 — created={created}, updated={updated}")

        @app.cli.command("seed-demo-all")
        def seed_demo_all():
            """
            Convenience: run the full DEMO seed suite in order.
            """
            # Reuse the functions above
            ctx = app.test_request_context(); ctx.push()
            try:
                # Core + masters + roles
                app.invoke(app.cli.commands["seed-core"])
                app.invoke(app.cli.commands["seed-masters"])
                app.invoke(app.cli.commands["seed-more-masters"])
                app.invoke(app.cli.commands["seed-auth"])
                # Employees + Users
                app.invoke(app.cli.commands["seed-employees-10"])
                app.invoke(app.cli.commands["seed-users-10"])
            finally:
                ctx.pop()
            click.echo("All demo seeds done: core, masters, auth, employees(1..10), users(emp1..emp10).")


        @app.cli.command("seed-employees-range")
        @click.option("--start", default=1, show_default=True, type=int, help="Start employee id")
        @click.option("--end", default=10, show_default=True, type=int, help="End employee id (inclusive)")
        def seed_employees_range(start: int, end: int):
            """
            Ensure employees with IDs in [start..end] exist under DEMO company.
            Populates basics so importer & reports work. Safe to run multiple times.
            """
            from sqlalchemy import text
            from hrms_api.models.master import Company, Location, Department, Designation, Grade, CostCenter
            from hrms_api.models.employee import Employee

            if start > end:
                click.echo("start must be <= end"); return

            c = Company.query.filter_by(code="DEMO").first()
            if not c:
                click.echo("Company DEMO missing. Run: flask seed-core"); return

            # Ensure baseline masters exist
            loc = Location.query.filter_by(company_id=c.id, name="Pune").first()
            if not loc:
                loc = Location(company_id=c.id, name="Pune"); db.session.add(loc); db.session.commit()
            dept = Department.query.filter_by(company_id=c.id, name="Engineering").first()
            if not dept:
                dept = Department(company_id=c.id, name="Engineering"); db.session.add(dept); db.session.commit()
            des = Designation.query.filter_by(department_id=dept.id, name="Software Engineer").first()
            if not des:
                des = Designation(department_id=dept.id, name="Software Engineer"); db.session.add(des); db.session.commit()
            grd = Grade.query.filter_by(name="G1").first()
            if not grd:
                grd = Grade(name="G1"); db.session.add(grd); db.session.commit()
            cc = CostCenter.query.filter_by(code="CC-ENG").first()
            if not cc:
                cc = CostCenter(code="CC-ENG", name="Engineering"); db.session.add(cc); db.session.commit()

            created = 0
            updated = 0
            for i in range(start, end + 1):
                email = f"emp{i}@demo.local"
                code = f"E-{i:04d}"

                emp = Employee.query.get(i)  # pin specific IDs
                if not emp:
                    emp = Employee(
                        id=i,
                        company_id=c.id,
                        location_id=loc.id,
                        department_id=dept.id,
                        designation_id=des.id,
                        grade_id=grd.id,
                        cost_center_id=cc.id,
                        code=code,
                        email=email,
                        first_name=f"Emp{i}",
                        last_name="Demo",
                        doj=datetime.utcnow().date(),
                        employment_type="fulltime",
                        status="active",
                    )
                    db.session.add(emp)
                    created += 1
                else:
                    # Make sure key fields exist
                    changed = False
                    if not getattr(emp, "email", None): emp.email = email; changed = True
                    if not getattr(emp, "code",  None): emp.code  = code;  changed = True
                    if getattr(emp, "status", None) != "active": emp.status = "active"; changed = True
                    if changed: updated += 1

            db.session.commit()

            # Reset the sequence (Postgres) because we manually set IDs
            try:
                db.session.execute(text(
                    "SELECT setval(pg_get_serial_sequence('employees','id'), (SELECT MAX(id) FROM employees))"
                ))
                db.session.commit()
            except Exception:
                pass

            click.echo(f"Employees ensured in [{start}..{end}] → created={created}, updated={updated}")

        @app.cli.command("seed-users-range")
        @click.option("--start", default=1, show_default=True, type=int)
        @click.option("--end", default=10, show_default=True, type=int)
        @click.option("--password", default="4445", show_default=True, type=str)
        def seed_users_range(start: int, end: int, password: str):
            """
            Ensure users emp{start}..emp{end} exist with password (default 4445).
            """
            from hrms_api.models.user import User

            if start > end:
                click.echo("start must be <= end"); return

            created = 0
            updated = 0
            for i in range(start, end + 1):
                email = f"emp{i}@demo.local"
                full_name = f"Employee {i}"
                u = User.query.filter_by(email=email).first()
                if not u:
                    u = User(email=email, full_name=full_name, status="active")
                    u.set_password(password)
                    db.session.add(u)
                    created += 1
                else:
                    changed = False
                    if getattr(u, "full_name", "") != full_name:
                        u.full_name = full_name; changed = True
                    if getattr(u, "status", "active") != "active":
                        u.status = "active"; changed = True
                    if changed: updated += 1

            db.session.commit()
            click.echo(f"Users ensured emp{start}..emp{end} (password={password}) → created={created}, updated={updated}")

        @app.cli.command("seed-demo-range")
        @click.option("--start", default=1, show_default=True, type=int)
        @click.option("--end", default=10, show_default=True, type=int)
        @click.option("--password", default="4445", show_default=True, type=str)
        def seed_demo_range(start: int, end: int, password: str):
            """
            Convenience wrapper: creates employees [start..end] under DEMO and users emp{start}..emp{end}.
            """
            # call the commands directly
            ctx = app.test_request_context(); ctx.push()
            try:
                app.invoke(app.cli.commands["seed-employees-range"], start=start, end=end)
                app.invoke(app.cli.commands["seed-users-range"], start=start, end=end, password=password)
            finally:
                ctx.pop()
            click.echo(f"Done: employees [{start}..{end}] and users emp{start}..emp{end} (password={password})")

        # ---------------- DEMO BULK SEEDERS ----------------

        # ---------------- DEMO BULK SEEDERS (fixed: no app.invoke) ----------------

    def _ensure_demo_masters():
        from hrms_api.models.master import Company, Location, Department, Designation, Grade, CostCenter
        # Company
        c = Company.query.filter_by(code="DEMO").first()
        if not c:
            c = Company(code="DEMO", name="Demo Co")
            db.session.add(c); db.session.commit()
        # Location
        loc = Location.query.filter_by(company_id=c.id, name="Pune").first()
        if not loc:
            loc = Location(company_id=c.id, name="Pune"); db.session.add(loc); db.session.commit()
        # Department
        dept = Department.query.filter_by(company_id=c.id, name="Engineering").first()
        if not dept:
            dept = Department(company_id=c.id, name="Engineering"); db.session.add(dept); db.session.commit()
        # Designation
        des = Designation.query.filter_by(department_id=dept.id, name="Software Engineer").first()
        if not des:
            des = Designation(department_id=dept.id, name="Software Engineer"); db.session.add(des); db.session.commit()
        # Grade
        grd = Grade.query.filter_by(name="G1").first()
        if not grd:
            grd = Grade(name="G1"); db.session.add(grd); db.session.commit()
        # Cost center
        cc = CostCenter.query.filter_by(code="CC-ENG").first()
        if not cc:
            cc = CostCenter(code="CC-ENG", name="Engineering"); db.session.add(cc); db.session.commit()
        return c, loc, dept, des, grd, cc

    def _seed_employees_range_impl(start: int, end: int):
        from sqlalchemy import text
        from hrms_api.models.employee import Employee
        if start > end:
            raise ValueError("start must be <= end")

        c, loc, dept, des, grd, cc = _ensure_demo_masters()

        created = updated = 0
        for i in range(start, end + 1):
            email = f"emp{i}@demo.local"
            code  = f"E-{i:04d}"
            emp = Employee.query.get(i)  # pin exact IDs
            if not emp:
                emp = Employee(
                    id=i,
                    company_id=c.id, location_id=loc.id,
                    department_id=dept.id, designation_id=des.id,
                    grade_id=grd.id, cost_center_id=cc.id,
                    code=code, email=email,
                    first_name=f"Emp{i}", last_name="Demo",
                    doj=datetime.utcnow().date(),
                    employment_type="fulltime", status="active",
                )
                db.session.add(emp); created += 1
            else:
                changed = False
                if not getattr(emp, "email", None): emp.email = email; changed = True
                if not getattr(emp, "code",  None): emp.code  = code;  changed = True
                if getattr(emp, "status", None) != "active": emp.status = "active"; changed = True
                if changed: updated += 1
        db.session.commit()

        # reset PG sequence if IDs were set manually
        try:
            db.session.execute(text(
                "SELECT setval(pg_get_serial_sequence('employees','id'), (SELECT MAX(id) FROM employees))"
            ))
            db.session.commit()
        except Exception:
            pass

        return created, updated

    def _seed_users_range_impl(start: int, end: int, password: str = "4445"):
        from hrms_api.models.user import User
        if start > end:
            raise ValueError("start must be <= end")

        created = updated = 0
        for i in range(start, end + 1):
            email = f"emp{i}@demo.local"
            full_name = f"Employee {i}"
            u = User.query.filter_by(email=email).first()
            if not u:
                u = User(email=email, full_name=full_name, status="active")
                u.set_password(password)
                db.session.add(u); created += 1
            else:
                changed = False
                if getattr(u, "full_name", "") != full_name:
                    u.full_name = full_name; changed = True
                if getattr(u, "status", "active") != "active":
                    u.status = "active"; changed = True
                if changed: updated += 1
        db.session.commit()
        return created, updated

    @app.cli.command("seed-employees-range")
    @click.option("--start", default=1, show_default=True, type=int)
    @click.option("--end", default=10, show_default=True, type=int)
    def seed_employees_range(start: int, end: int):
        """Ensure employees with IDs [start..end] exist under DEMO."""
        try:
            created, updated = _seed_employees_range_impl(start, end)
            click.echo(f"Employees ensured in [{start}..{end}] → created={created}, updated={updated}")
        except ValueError as e:
            click.echo(str(e))

    @app.cli.command("seed-users-range")
    @click.option("--start", default=1, show_default=True, type=int)
    @click.option("--end", default=10, show_default=True, type=int)
    @click.option("--password", default="4445", show_default=True, type=str)
    def seed_users_range(start: int, end: int, password: str):
        """Ensure users emp{start}..emp{end} exist with given password."""
        try:
            created, updated = _seed_users_range_impl(start, end, password)
            click.echo(f"Users ensured emp{start}..emp{end} (password={password}) → created={created}, updated={updated}")
        except ValueError as e:
            click.echo(str(e))

    @app.cli.command("seed-demo-range")
    @click.option("--start", default=1, show_default=True, type=int)
    @click.option("--end", default=10, show_default=True, type=int)
    @click.option("--password", default="4445", show_default=True, type=str)
    def seed_demo_range(start: int, end: int, password: str):
        """Wrapper: employees + users in one go."""
        try:
            e_created, e_updated = _seed_employees_range_impl(start, end)
            u_created, u_updated = _seed_users_range_impl(start, end, password)
            click.echo(
                f"Done: employees [{start}..{end}] (created={e_created}, updated={e_updated}) "
                f"and users emp{start}..emp{end} (created={u_created}, updated={u_updated}, password={password})"
            )
        except ValueError as e:
            click.echo(str(e))

    # Convenience aliases
    @app.cli.command("seed-employees-10")
    def seed_employees_10():
        created, updated = _seed_employees_range_impl(1, 10)
        click.echo(f"Employees ensured in [1..10] → created={created}, updated={updated}")

    @app.cli.command("seed-users-10")
    def seed_users_10():
        created, updated = _seed_users_range_impl(1, 10, "4445")
        click.echo(f"Users ensured emp1..emp10 (password=4445) → created={created}, updated={updated}")

    @app.cli.command("seed-demo-all")
    def seed_demo_all():
        e_created, e_updated = _seed_employees_range_impl(1, 10)
        u_created, u_updated = _seed_users_range_impl(1, 10, "4445")
        click.echo(
            f"All demo seeded: employees [1..10] (created={e_created}, updated={e_updated}); "
            f"users emp1..emp10 (created={u_created}, updated={u_updated}, password=4445)"
        )


    @app.cli.command("seed-leave-types")
    def seed_leave_types():
        from hrms_api.models.master import Company
        from hrms_api.models.leave import LeaveType
        c = Company.query.filter_by(code="DEMO").first()
        if not c:
            click.echo("Run seed-core first"); return
        defs = [
            ("CL","Casual Leave",1.0, True,  False),
            ("SL","Sick Leave",  0.5, True,  False),
            ("PL","Privilege",   1.5, True,  True),
        ]
        created = 0
        for code,name,accrual,paid,carry in defs:
            t = LeaveType.query.filter_by(company_id=c.id, code=code).first()
            if not t:
                t = LeaveType(company_id=c.id, code=code, name=name,
                              accrual_per_month=accrual, paid=paid,
                              carry_forward_limit=30 if carry else None)
                db.session.add(t); created += 1
        db.session.commit()
        click.echo(f"Leave types ensured: created={created}")

    @app.cli.command("seed-leave-balances-10")
    def seed_leave_balances_10():
        from hrms_api.models.leave import LeaveType, LeaveBalance
        from hrms_api.models.employee import Employee

        emp_ids = [e.id for e in Employee.query.filter(Employee.id.between(1,10)).all()]
        types = LeaveType.query.all()
        if not emp_ids or not types:
            click.echo("Need employees 1..10 and leave types. Run seed-demo-all and seed-leave-types."); return
        created = 0
        for eid in emp_ids:
            for t in types:
                b = LeaveBalance.query.filter_by(employee_id=eid, leave_type_id=t.id).first()
                if not b:
                    # starter packs
                    start_bal = 12.0 if t.code=="CL" else 7.0 if t.code=="SL" else 15.0
                    b = LeaveBalance(employee_id=eid, leave_type_id=t.id, balance=start_bal, ytd_accrued=start_bal, ytd_taken=0)
                    db.session.add(b); created += 1
        db.session.commit()
        click.echo(f"Leave balances ensured for emp1..10 across {len(types)} types: created={created}")



    @app.cli.command("seed-rbac")
    @with_appcontext
    def seed_rbac():
        from hrms_api.seed_rbac import run
        click.echo(run())



    @app.cli.command("grant-admin")
    @click.argument("email")
    def grant_admin(email):
        """Attach 'admin' role to a user by email."""
        from hrms_api.extensions import db
        from hrms_api.models.user import User
        from hrms_api.models.security import Role, UserRole

        u = User.query.filter_by(email=email).first()
        if not u:
            click.echo(f"User {email} not found"); return
        admin = Role.query.filter_by(code="admin").first()
        if not admin:
            admin = Role(code="admin"); db.session.add(admin); db.session.commit()
        has = any(ur.role_id == admin.id for ur in u.user_roles)
        if not has:
            db.session.add(UserRole(user_id=u.id, role_id=admin.id)); db.session.commit()
        click.echo(f"Granted 'admin' to {email}")

    @app.cli.command("rbac-grant-all")
    @click.argument("role_code")
    def rbac_grant_all(role_code):
        """Grant ALL existing permissions to a role (e.g., 'hr' or 'admin')."""
        from hrms_api.extensions import db
        from hrms_api.models.security import Role, Permission, RolePermission

        role = Role.query.filter_by(code=role_code).first()
        if not role:
            click.echo(f"Role {role_code} not found"); return
        perm_ids = [p.id for p in Permission.query.all()]
        existing = {(rp.role_id, rp.permission_id) for rp in role.permissions}
        new_links = 0
        for pid in perm_ids:
            key = (role.id, pid)
            if key not in existing:
                db.session.add(RolePermission(role_id=role.id, permission_id=pid))
                new_links += 1
        db.session.commit()
        click.echo(f"Granted {new_links} permissions to role '{role_code}'")



    return app
