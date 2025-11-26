import os
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import current_app

from hrms_api.extensions import db
from hrms_api.models.face_profile import EmployeeFaceProfile
from hrms_api.models.face_log import FaceAttendanceLog
from hrms_api.models.attendance_punch import AttendancePunch
from hrms_api.models.employee import Employee
from hrms_api.models.master import Location
from hrms_api.services.face_engine import FaceEngine
from hrms_api.services.geofence import GeofenceService

class FaceAttendanceService:
    
    UPLOAD_FOLDER = "static/uploads/faces"
    # Threshold for cosine similarity (0..1). 
    # DeepFace Facenet512 default distance threshold is ~0.3.
    # Similarity = 1 - distance. So 0.7.
    # We'll be slightly more lenient or strict based on testing. 
    # Let's start with 0.6.
    SIMILARITY_THRESHOLD = 0.6 
    
    @staticmethod
    def _save_image(file_storage, employee_id) -> str:
        # Ensure directory exists
        base_dir = os.path.join(current_app.root_path, FaceAttendanceService.UPLOAD_FOLDER)
        os.makedirs(base_dir, exist_ok=True)
        
        ext = os.path.splitext(file_storage.filename)[1]
        if not ext:
            ext = ".jpg" # Default
        filename = f"{employee_id}_{uuid.uuid4().hex}{ext}"
        path = os.path.join(base_dir, filename)
        file_storage.save(path)
        
        # Return relative path for DB
        return f"{FaceAttendanceService.UPLOAD_FOLDER}/{filename}"

    @staticmethod
    def enroll_face(employee_id: int, image_file, label: str = None) -> dict:
        # 1. Save image
        rel_path = FaceAttendanceService._save_image(image_file, employee_id)
        abs_path = os.path.join(current_app.root_path, rel_path)
        
        # 2. Get embedding
        embedding = FaceEngine.get_embedding(abs_path)
        if not embedding:
            # Optional: delete file if invalid?
            return {"success": False, "error": "Face detection failed or multiple faces found"}
            
        # 3. Save profile
        profile = EmployeeFaceProfile(
            employee_id=employee_id,
            image_url=rel_path,
            embedding=embedding,
            label=label,
            is_active=True
        )
        db.session.add(profile)
        db.session.commit()
        
        return {"success": True, "profile_id": profile.id}

    @staticmethod
    def process_self_punch(employee_id: int, image_file, lat: float, lon: float, punch_type: str, device_id: str = None) -> dict:
        # 1. Authenticate & Get Employee
        emp = Employee.query.get(employee_id)
        if not emp:
            return {"success": False, "error": "Employee not found"}

        # 2. Save evidence image
        rel_path = FaceAttendanceService._save_image(image_file, employee_id)
        abs_path = os.path.join(current_app.root_path, rel_path)
        
        # 3. Face Verification
        embedding = FaceEngine.get_embedding(abs_path)
        
        log = FaceAttendanceLog(
            employee_id=employee_id,
            device_id=device_id,
            req_lat=lat,
            req_lng=lon,
            punch_type=punch_type,
            created_at=datetime.utcnow(),
            location_status="UNKNOWN",
            face_status="UNKNOWN",
            result="PENDING"
        )
        db.session.add(log) # Add to session to get ID if needed, but we commit later
        
        if not embedding:
            log.face_status = "NO_FACE"
            log.result = "REJECTED"
            log.error_message = "Face detection failed"
            db.session.commit()
            return {"success": False, "error": "Face detection failed"}

        # Match against active profiles
        profiles = EmployeeFaceProfile.query.filter_by(employee_id=employee_id, is_active=True).all()
        if not profiles:
            log.face_status = "NO_PROFILE"
            log.result = "REJECTED"
            log.error_message = "No active face profiles found"
            db.session.commit()
            return {"success": False, "error": "No face profiles found"}
            
        best_sim = -1.0
        for p in profiles:
            sim = FaceEngine.compute_similarity(embedding, p.embedding)
            if sim > best_sim:
                best_sim = sim
        
        log.face_similarity = best_sim
        
        if best_sim < FaceAttendanceService.SIMILARITY_THRESHOLD:
            log.face_status = "MISMATCH"
            log.result = "REJECTED"
            log.error_message = f"Face mismatch (score: {best_sim:.2f})"
            db.session.commit()
            return {"success": False, "error": "Face mismatch"}
            
        log.face_status = "MATCH"
        
        # 4. Geofence Verification
        loc = emp.location
        if not loc:
             log.location_status = "NO_LOCATION_ASSIGNED"
             log.result = "REJECTED"
             log.error_message = "No location assigned to employee"
             db.session.commit()
             return {"success": False, "error": "No location assigned to employee"}
             
        site_lat = float(loc.geo_lat) if loc.geo_lat else None
        site_lon = float(loc.geo_lon) if loc.geo_lon else None
        radius = loc.geo_radius_m or 200
        
        if site_lat is None or site_lon is None:
            log.location_status = "NO_GEOFENCE"
            # Allow if no geofence configured?
            is_inside = True
            dist = 0
        else:
            is_inside, dist = GeofenceService.check_geofence(lat, lon, site_lat, site_lon, radius)
            log.distance_m = dist
            log.location_status = "INSIDE_GEOFENCE" if is_inside else "OUTSIDE_GEOFENCE"
            
        if not is_inside:
            log.result = "REJECTED"
            log.error_message = f"Outside geofence ({dist:.0f}m)"
            db.session.commit()
            return {"success": False, "error": f"You are {dist:.0f}m away from location"}
            
        # 5. Create Punch
        punch = AttendancePunch(
            company_id=emp.company_id,
            employee_id=emp.id,
            ts=datetime.utcnow(),
            direction=punch_type.lower(),
            method="selfie",
            device_id=device_id,
            lat=lat,
            lon=lon,
            location_id=loc.id,
            photo_url=rel_path,
            face_score=best_sim
        )
        db.session.add(punch)
        db.session.flush()
        
        log.punch_id = punch.id
        log.result = "MARKED"
        db.session.commit()
        
        return {
            "success": True, 
            "punch_id": punch.id, 
            "similarity": best_sim,
            "location_status": log.location_status,
            "distance": log.distance_m
        }

    @staticmethod
    def verify_face_match(image_file) -> dict:
        # 1. Save temp image
        base_dir = os.path.join(current_app.root_path, FaceAttendanceService.UPLOAD_FOLDER, "temp")
        os.makedirs(base_dir, exist_ok=True)
        ext = os.path.splitext(image_file.filename)[1] or ".jpg"
        filename = f"verify_{uuid.uuid4().hex}{ext}"
        abs_path = os.path.join(base_dir, filename)
        image_file.save(abs_path)
        
        try:
            # 2. Get embedding
            embedding = FaceEngine.get_embedding(abs_path)
            if not embedding:
                return {"success": False, "error": "Face detection failed"}
                
            # 3. Compare with ALL active profiles
            profiles = EmployeeFaceProfile.query.filter_by(is_active=True).all()
            
            best_sim = -1.0
            best_profile = None
            
            for p in profiles:
                sim = FaceEngine.compute_similarity(embedding, p.embedding)
                if sim > best_sim:
                    best_sim = sim
                    best_profile = p
            
            if best_sim >= FaceAttendanceService.SIMILARITY_THRESHOLD:
                emp = best_profile.employee
                return {
                    "success": True,
                    "match_found": True,
                    "employee": {
                        "id": emp.id,
                        "name": f"{emp.first_name} {emp.last_name}",
                        "email": emp.email,
                        "code": emp.code
                    },
                    "similarity": best_sim,
                    "profile_id": best_profile.id
                }
            else:
                return {
                    "success": True,
                    "match_found": False,
                    "best_similarity": best_sim
                }
                
        finally:
            # Cleanup temp file
            if os.path.exists(abs_path):
                os.remove(abs_path)
