from datetime import datetime
from hrms_api.extensions import db

class FaceAttendanceLog(db.Model):
    __tablename__ = "face_attendance_logs"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    punch_id = db.Column(db.Integer, db.ForeignKey("attendance_punches.id", ondelete="SET NULL"), nullable=True)
    
    device_id = db.Column(db.String(255), nullable=True)
    
    req_lat = db.Column(db.Numeric(9, 6), nullable=True)
    req_lng = db.Column(db.Numeric(9, 6), nullable=True)
    distance_m = db.Column(db.Float, nullable=True)
    
    location_status = db.Column(db.String(50), nullable=False) # INSIDE_GEOFENCE, OUTSIDE_GEOFENCE, NO_GEOFENCE
    face_status = db.Column(db.String(50), nullable=False) # MATCH, MISMATCH, NO_FACE, MULTIPLE_FACES, ENGINE_ERROR
    face_similarity = db.Column(db.Float, nullable=True)
    
    result = db.Column(db.String(50), nullable=False) # MARKED, REJECTED
    punch_type = db.Column(db.String(10), nullable=True) # IN, OUT, AUTO
    
    error_code = db.Column(db.String(100), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    employee = db.relationship("Employee")
    punch = db.relationship("AttendancePunch")
