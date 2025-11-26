from datetime import datetime
from sqlalchemy.dialects.postgresql import JSONB
from hrms_api.extensions import db

class EmployeeFaceProfile(db.Model):
    __tablename__ = "employee_face_profiles"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    image_url = db.Column(db.Text, nullable=True)
    embedding = db.Column(JSONB, nullable=False)  # Array of floats
    embedding_version = db.Column(db.String(50), default="v1", nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    label = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    employee = db.relationship("Employee", backref=db.backref("face_profiles", lazy="dynamic"))
