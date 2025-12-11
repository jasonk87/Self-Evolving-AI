import requests
from typing import Dict, Optional

def get_weather(location: str, api_key: str) -> Optional[Dict]:
    """
    Retrieves the current weather information for a specified location.

    Args:
        location (str): The city or location for which to retrieve weather data.
        api_key (str): The API key for accessing the weather service.

    Returns:
        Optional[Dict]: A dictionary containing weather information, or None if an error occurred.
                       The dictionary will contain keys like 'temperature', 'description', 'humidity', etc.
                       The exact keys depend on the weather API used.
    """
    try:
        base_url = "http://api.openweathermap.org/data/2.5/weather"  # Example API, replace with your preferred service
        params = {
            "q": location,
            "appid": api_key,
            "units": "metric"  # Use metric units
        }
        response = requests.get(base_url, params=params)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        
        # Extract relevant weather information
        weather_data = {
            "temperature": data["main"]["temp"],
            "description": data["weather"][0]["description"],
            "humidity": data["main"]["humidity"],
            "wind_speed": data["wind"]["speed"],
            "city": data["name"]
        }
        return weather_data
    except requests.exceptions.RequestException as e:
        print(f"Error fetching weather data: {e}")
        return None
    except (KeyError, TypeError) as e:
        print(f"Error parsing weather data: {e}")
        return None