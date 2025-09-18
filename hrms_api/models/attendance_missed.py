# apps/backend/hrms_api/models/attendance_missed.py
from sqlalchemy import Column, Integer, Date, Time, Text, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from hrms_api.extensions import db

class MissedPunchRequest(db.Model):
    __tablename__ = "missed_punch_requests"

    # --- PRIMARY KEY (fix) ---
    id = db.Column(db.Integer, primary_key=True)

    # --- Fields ---
    employee_id  = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    req_date     = db.Column(db.Date, nullable=False)
    in_time      = db.Column(db.Time, nullable=True)
    out_time     = db.Column(db.Time, nullable=True)
    reason       = db.Column(db.Text, nullable=True)

    status       = db.Column(db.String(20), nullable=False, default="pending", index=True)  # pending/approved/rejected
    approved_by  = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at  = db.Column(db.DateTime, nullable=True)
    approver_note = db.Column(db.Text, nullable=True)

    # --- Timestamps (optional but handy) ---
    created_at   = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    updated_at   = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now(), nullable=False)

    # --- Relationships ---
    employee = db.relationship(
        "Employee",
        backref=db.backref("missed_punch_requests", cascade="all, delete-orphan", passive_deletes=True),
    )
    approver = db.relationship("User", foreign_keys=[approved_by])
