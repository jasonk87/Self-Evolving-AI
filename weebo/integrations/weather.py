"""Weather (OpenWeather, the user's own key) and approximate location (IP based)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from . import http
from .base import IntegrationError, Outcome

if TYPE_CHECKING:
    from ..app import WeeboApp


async def get_weather(app: "WeeboApp", location: str) -> Outcome:
    location = (location or "").strip()
    if not location:
        raise IntegrationError("Give a location, for example 'Austin, TX'.")
    data = await http.get_json("https://api.openweathermap.org/data/2.5/weather",
                               {"q": location, "appid": os.environ["OPENWEATHER_API_KEY"], "units": "imperial"})
    if str(data.get("cod", "200")) != "200":
        raise IntegrationError(f"OpenWeather: {data.get('message') or data.get('cod')}")
    try:
        city, country = data["name"], data.get("sys", {}).get("country", "")
        description = data["weather"][0]["description"]
        temp_f, feels_f = float(data["main"]["temp"]), float(data["main"].get("feels_like", data["main"]["temp"]))
        humidity, wind = data["main"]["humidity"], data.get("wind", {}).get("speed", 0)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise IntegrationError(f"OpenWeather returned an unexpected answer ({exc}).") from exc
    temp_c = (temp_f - 32) * 5 / 9
    text = (f"Weather in {city}{', ' + country if country else ''}: {description}, {temp_f:.0f}°F ({temp_c:.0f}°C), "
            f"feels like {feels_f:.0f}°F, humidity {humidity}%, wind {wind} mph.")
    return Outcome(text)


async def get_user_location(app: "WeeboApp") -> Outcome:
    data = await http.get_json("https://ipinfo.io/json", timeout=8)
    parts = [data.get(k) for k in ("city", "region", "country") if data.get(k)]
    if not parts:
        raise IntegrationError("Couldn't work out a location from this machine's IP address.")
    return Outcome(f"Approximate location (from IP, not GPS): {', '.join(parts)}"
                   + (f"; coordinates {data['loc']}" if data.get("loc") else "") + ".")
