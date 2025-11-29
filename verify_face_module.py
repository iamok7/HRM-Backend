import sys
import os

# Add current dir to path
sys.path.append(os.getcwd())

def test_geofence():
    from hrms_api.services.geofence import GeofenceService
    # Pune coordinates
    lat1, lon1 = 18.5204, 73.8567
    # Mumbai coordinates
    lat2, lon2 = 19.0760, 72.8777
    dist = GeofenceService.calculate_distance(lat1, lon1, lat2, lon2)
    print(f"Distance Pune-Mumbai: {dist/1000:.2f} km")
    assert dist > 100000 # > 100km

def test_face_engine_import():
    print("Importing FaceEngine...")
    from hrms_api.services.face_engine import FaceEngine
    print("FaceEngine imported.")

if __name__ == "__main__":
    try:
        test_geofence()
        test_face_engine_import()
        print("Verification successful!")
    except Exception as e:
        print(f"Verification failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
