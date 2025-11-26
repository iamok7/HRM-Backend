import math
from typing import Optional, Tuple

class GeofenceService:
    @staticmethod
    def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculate the great circle distance between two points 
        on the earth (specified in decimal degrees) using Haversine formula.
        Returns distance in meters.
        """
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return float('inf')

        # Convert to float just in case they are Decimal or strings
        lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])

        R = 6371000  # Radius of earth in meters

        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)

        a = math.sin(dphi / 2.0)**2 + \
            math.cos(phi1) * math.cos(phi2) * \
            math.sin(dlambda / 2.0)**2
        
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c

    @staticmethod
    def check_geofence(user_lat: float, user_lon: float, site_lat: float, site_lon: float, radius_m: int) -> Tuple[bool, float]:
        """
        Returns (is_inside, distance_m)
        """
        dist = GeofenceService.calculate_distance(user_lat, user_lon, site_lat, site_lon)
        return dist <= radius_m, dist
