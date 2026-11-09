"""Current weather and forecast from Open-Meteo (free, no key), so weather questions need no slow
web search, and the screen and Pip's voice use the same source (Ziggletron bug P-08).
Only the place name and coordinates leave the Pi.

Used by pip-thinker (what Claude is told) and pip-web (the weather box and the face's clothing on
the TV page). `dress()` says what the face wears, as Ziggletron's did."""
import asyncio
import time

import httpx

CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
         51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
         66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
         77: "snow grains", 80: "rain showers", 81: "rain showers", 82: "heavy rain showers", 85: "snow showers",
         86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


RETRY_S = 120         # after a failed fetch, wait this long before trying again (not on every request)
STALE_S = 3 * 3600    # older than this, it isn't "now" any more: not shown or told at all


class Weather:
    def __init__(self, place: str, refresh_s: float = 1800):
        self.place, self.refresh_s = place, refresh_s
        self.coords = None
        self.summary = ""
        self.data: dict = {}   # the same forecast, for the screen: now, wear, days
        self.fetched = 0.0     # when the last fetch worked
        self.tried = 0.0       # when a fetch was last started
        self._task = None

    async def screen(self) -> dict:
        """For the TV page: {"temp", "feels", "sky", "wear", "days": [{"name", "sky", "high", "low", "rain"}]}."""
        await self._refresh()
        return self.cached()

    async def screen_within(self, wait: float) -> dict:
        """The screen's weather, waiting at most `wait` seconds: a slow fetch carries on for next time."""
        if self._task is None or self._task.done() or self._task.get_loop() is not asyncio.get_running_loop():
            self._task = asyncio.ensure_future(self.screen())
        try:
            return await asyncio.wait_for(asyncio.shield(self._task), wait)
        except Exception:
            return self.cached()

    def cached(self) -> dict:
        return self.data if self.fresh() else {}

    def fresh(self) -> bool:
        return time.time() - self.fetched < STALE_S

    async def text(self) -> str:
        await self._refresh()
        return self.summary if self.fresh() else ""

    async def _refresh(self) -> None:
        now = time.time()
        if (now - self.fetched > self.refresh_s or not self.summary) and now - self.tried > RETRY_S:
            self.tried = now
            try:
                await self._fetch()
            except Exception:
                pass  # keep the last summary while it's fresh; Pip can still search if needed

    async def _fetch(self) -> None:
        async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "pip-assistant"}) as http:
            if not self.coords:
                name, _, region = self.place.partition(",")
                g = (await http.get("https://geocoding-api.open-meteo.com/v1/search",
                                    params={"name": name.strip(), "count": 10, "country_code": "US"})).json()
                best = next((r for r in g.get("results", []) if region.strip().lower() in (r.get("admin1", "").lower())),
                            (g.get("results") or [None])[0])
                if not best:
                    return
                self.coords = (best["latitude"], best["longitude"])
            lat, lon = self.coords
            w = (await http.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon, "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": 3, "timezone": "auto"})).json()
        c, d = w["current"], w["daily"]
        days = ["today", "tomorrow", "the day after tomorrow"]
        parts = [f"Now in {self.place}: {round(c['temperature_2m'])}°F (feels {round(c['apparent_temperature'])}°F), "
                 f"{CODES.get(c['weather_code'], 'unknown')}, wind {round(c['wind_speed_10m'])} mph."]
        for i, day in enumerate(days):
            parts.append(f"{day.capitalize()}: {CODES.get(d['weather_code'][i], 'unknown')}, high {round(d['temperature_2m_max'][i])}°F, "
                         f"low {round(d['temperature_2m_min'][i])}°F, {d['precipitation_probability_max'][i]}% chance of rain or snow.")
        self.summary = " ".join(parts)
        self.data = {"temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
                     "sky": CODES.get(c["weather_code"], ""),
                     "wear": dress(c["weather_code"], c["apparent_temperature"], bool(c.get("is_day", 1))),
                     "days": [{"name": ["Today", "Tomorrow", "Next day"][i], "sky": CODES.get(d["weather_code"][i], ""),
                               "high": round(d["temperature_2m_max"][i]), "low": round(d["temperature_2m_min"][i]),
                               "rain": d["precipitation_probability_max"][i]} for i in range(3)]}
        self.fetched = time.time()


COLD_F, WARM_F = 40, 65   # feels this cold or colder: a warm hat · sunny and this warm or more: sunglasses


def dress(code, feels, day: bool = True) -> str:
    """What the face on the TV wears for this weather (as Ziggletron's did): "storm", "umbrella", "snow"
    (a warm hat in falling snow), "beanie", "sunglasses" or "" for nothing. The TV page swaps the warm hat
    for a Santa hat in the holidays."""
    if code in (95, 96, 99):
        return "storm"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "umbrella"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if feels is not None and feels <= COLD_F:
        return "beanie"
    if code in (0, 1) and day and feels is not None and feels >= WARM_F:
        return "sunglasses"
    return ""
