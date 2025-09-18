from datetime import datetime
from hrms_api.extensions import db

class Holiday(db.Model):
    __tablename__ = "holidays"
    id = db.Column(db.Integer, primary_key=True)
    company_id  = db.Column(db.Integer, db.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True)
    date        = db.Column(db.Date, nullable=False)
    name        = db.Column(db.String(120), nullable=False)
    is_optional = db.Column(db.Boolean, nullable=False, default=False)
    created_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        db.UniqueConstraint("company_id", "location_id", "date", name="uq_holiday_company_location_date"),
    )

class WeeklyOffRule(db.Model):
    __tablename__ = "weekly_off_rules"
    id = db.Column(db.Integer, primary_key=True)
    company_id  = db.Column(db.Integer, db.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True)
    weekday     = db.Column(db.SmallInteger, nullable=False)  # 0=Mon .. 6=Sun
    is_alternate = db.Column(db.Boolean, nullable=False, default=False)  # true => use week_numbers
    week_numbers = db.Column(db.String(20), nullable=True)              # e.g. "2,4"
    created_at   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        db.UniqueConstraint("company_id", "location_id", "weekday", "is_alternate", "week_numbers", name="uq_weekoff_rule"),
    )

class Shift(db.Model):
    __tablename__ = "shifts"
    id = db.Column(db.Integer, primary_key=True)
    company_id   = db.Column(db.Integer, db.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    code         = db.Column(db.String(20), nullable=False)
    name         = db.Column(db.String(60), nullable=False)
    start_time   = db.Column(db.Time, nullable=False)
    end_time     = db.Column(db.Time, nullable=False)
    break_minutes = db.Column(db.Integer, nullable=False, default=0)
    grace_minutes = db.Column(db.Integer, nullable=False, default=0)
    is_night     = db.Column(db.Boolean, nullable=False, default=False)
    created_at   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        db.UniqueConstraint("company_id", "code", name="uq_shift_company_code"),
    )
