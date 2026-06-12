import requests
from typing import Dict

def get_location_by_ip() -> Dict:
    """
    Gets the user's location based on their IP address using the ipinfo.io API.

    Args:
        None

    Returns:
        Dict: A dictionary containing location information (city, region, country, loc) or an error message.
              Example: {"city": "New York", "region": "NY", "country": "US", "loc": "40.7128,-74.0060"}
              Returns {"error": "..."} if an error occurs.
    """
    try:
        response = requests.get('https://ipinfo.io', timeout=5)
        response.raise_for_status()
        data = response.json()
        return {'city': data.get('city', 'N/A'), 'region': data.get('region', 'N/A'), 'country': data.get('country', 'N/A'), 'loc': data.get('loc', 'N/A'), 'ip': data.get('ip', 'N/A')}
    except requests.exceptions.RequestException as e:
        return {'error': f'Error getting location by IP: {e}'}
    except Exception as e:
        return {'error': f'An unexpected error occurred: {e}'}

def get_location_by_gps() -> Dict:
    """
    Attempts to get the user's location based on GPS.  This is a placeholder function,
    as direct GPS access is not typically available in server-side environments.

    Args:
        None

    Returns:
        Dict: A dictionary indicating that GPS location is not available.
              Example: {"message": "GPS location not available in this environment."}
    """
    return {'message': 'GPS location not available in this environment.'}

def get_user_location() -> Dict:
    """
    Gets the user's location, prioritizing GPS if available, otherwise using IP address.

    Args:
        None

    Returns:
        Dict: A dictionary containing location information (city, region, country, coordinates)
              or an error message.  If GPS is unavailable, attempts to get location by IP.
              Example (IP-based): {"city": "New York", "region": "NY", "country": "US", "loc": "40.7128,-74.0060"}
              Example (GPS unavailable): {"message": "GPS location not available in this environment."}
              Returns {"error": "..."} if an error occurs.
    """
    gps_location = get_location_by_gps()
    if 'error' not in gps_location and 'message' not in gps_location:
        return gps_location
    else:
        return get_location_by_ip()