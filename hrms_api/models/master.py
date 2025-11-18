from datetime import datetime

from sqlalchemy.sql import func

from hrms_api.extensions import db


class Company(db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    deleted_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def soft_delete(self):
        self.is_active = False
        self.deleted_at = func.now()


class Location(db.Model):
    """
    A physical work location / site.

    New fields for geo-fence based attendance:

      geo_lat, geo_lon   -> center point of the location (optional)
      geo_radius_m       -> allowed radius in meters for selfie/face attendance
                             (default: 500)
    """

    __tablename__ = "locations"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer,
        db.ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(120), nullable=False)

    # --- geofence config (used by selfie/face attendance) ---
    geo_lat = db.Column(db.Numeric(9, 6), nullable=True)
    geo_lon = db.Column(db.Numeric(9, 6), nullable=True)
    geo_radius_m = db.Column(db.Integer, nullable=False, default=500)

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("company_id", "name", name="uq_location_company_name"),
    )

    company = db.relationship(
        "Company", backref=db.backref("locations", lazy="dynamic")
    )

    # small helper for later if we need it in views/services
    def geo_center(self):
        """Return (lat, lon, radius_m) or (None, None, None) if not configured."""
        if self.geo_lat is None or self.geo_lon is None:
            return None, None, None
        return float(self.geo_lat), float(self.geo_lon), int(self.geo_radius_m or 0)


# Department — per company
class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer,
        db.ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(120), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("company_id", "name", name="uq_department_company_name"),
    )

    company = db.relationship(
        "Company", backref=db.backref("departments", lazy="dynamic")
    )


# Designation — per department
class Designation(db.Model):
    __tablename__ = "designations"

    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(120), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("department_id", "name", name="uq_designation_dept_name"),
    )

    department = db.relationship(
        "Department", backref=db.backref("designations", lazy="dynamic")
    )


# Grade — global
class Grade(db.Model):
    __tablename__ = "grades"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# Cost Center — global with unique code
class CostCenter(db.Model):
    __tablename__ = "cost_centers"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
