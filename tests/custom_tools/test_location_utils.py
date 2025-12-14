import pytest
from unittest.mock import patch
from ai_assistant.custom_tools.generated.location_utils import get_location_by_ip, get_location_by_gps, get_user_location
import requests

def test_get_location_by_ip_success():
    """Test successful retrieval of location data by IP."""
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "city": "New York",
            "region": "NY",
            "country": "US",
            "loc": "40.7128,-74.0060",
        }
        location = get_location_by_ip()
        assert "city" in location
        assert location["city"] == "New York"
        assert "region" in location
        assert location["region"] == "NY"
        assert "country" in location
        assert location["country"] == "US"
        assert "loc" in location
        assert location["loc"] == "40.7128,-74.0060"

def test_get_location_by_ip_api_error():
    """Test handling of API errors during IP-based location retrieval."""
    with patch("requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.RequestException("API Error")
        location = get_location_by_ip()
        assert "error" in location
        assert "Error getting location by IP" in location["error"]

def test_get_location_by_ip_unexpected_error():
    """Test handling of unexpected errors during IP-based location retrieval."""
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("Unexpected Error")
        location = get_location_by_ip()
        assert "error" in location
        assert "An unexpected error occurred" in location["error"]

def test_get_location_by_ip_bad_response():
    """Test handling of bad HTTP status codes."""
    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status.side_effect = requests.exceptions.HTTPError("Bad Request")
        location = get_location_by_ip()
        assert "error" in location
        assert "Error getting location by IP" in location["error"]

def test_get_location_by_gps():
    """Test that GPS location is not available."""
    location = get_location_by_gps()
    assert "message" in location
    assert location["message"] == "GPS location not available in this environment."

def test_get_user_location_gps_unavailable():
    """Test user location retrieval when GPS is unavailable, falling back to IP."""
    with patch("ai_assistant.custom_tools.generated.location_utils.get_location_by_gps") as mock_gps:
        mock_gps.return_value = {"message": "GPS location not available in this environment."}
        with patch("ai_assistant.custom_tools.generated.location_utils.get_location_by_ip") as mock_ip:
            mock_ip.return_value = {
                "city": "Test City",
                "region": "Test Region",
                "country": "Test Country",
                "loc": "1.23,4.56",
            }
            location = get_user_location()
            assert "city" in location
            assert location["city"] == "Test City"

def test_get_user_location_ip_error():
    """Test user location retrieval when GPS is unavailable and IP fails."""
    with patch("ai_assistant.custom_tools.generated.location_utils.get_location_by_gps") as mock_gps:
        mock_gps.return_value = {"message": "GPS location not available in this environment."}
        with patch("ai_assistant.custom_tools.generated.location_utils.get_location_by_ip") as mock_ip:
            mock_ip.return_value = {"error": "IP lookup failed"}
            location = get_user_location()
            assert "error" in location
            assert location["error"] == "IP lookup failed"