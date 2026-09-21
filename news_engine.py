"""
Forex Factory / FairEconomy High-Impact Economic News Engine
Features:
1. Automated Economic Calendar Retrieval from Forex Factory (FairEconomy JSON Feed)
2. Local Persistent Disk Caching (Refreshes every 4 hours to avoid API rate limits)
3. Zero-Dependency Built-in urllib & json Parsing (No external API keys required)
4. Robust ISO 8601 Timezone Normalization to UTC
5. High-Impact News Blackout Shield: Configurable 15m pre-news and 15m post-news pause window
6. Target Currency Filtering: Monitored currencies (Default: USD for Gold & Nasdaq)
7. Graceful Network Fallback: Automatically relies on cache during network drops
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
import os
import sys
import ssl
import urllib.error
import urllib.request

DEFAULT_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
TZ_IST = timezone(timedelta(hours=5, minutes=30))


def _get_ssl_context() -> ssl.SSLContext:
    """Returns an SSL context that prioritizes certifi CA bundle with fallback."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def format_dual_time(dt: Optional[datetime] = None, include_date: bool = False) -> str:
    """Formats datetime into dual UTC + IST string for easy local time reading."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    dt_ist = dt.astimezone(TZ_IST)
    if include_date:
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} UTC ({dt_ist.strftime('%Y-%m-%d %H:%M:%S')} IST)"
    return f"{dt.strftime('%H:%M:%S')} UTC ({dt_ist.strftime('%H:%M:%S')} IST)"


@dataclass
class EconomicEvent:
    title: str
    country: str
    date_utc: datetime
    impact: str
    forecast: str = ""
    previous: str = ""

    @property
    def time_utc(self) -> datetime:
        return self.date_utc

    @property
    def time_ist(self) -> datetime:
        return self.date_utc.astimezone(TZ_IST)

    @property
    def time_dual_str(self) -> str:
        return f"{self.date_utc.strftime('%Y-%m-%d %H:%M')} UTC ({self.time_ist.strftime('%H:%M')} IST)"

    @property
    def is_high_impact(self) -> bool:
        return self.impact.lower() == "high"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "country": self.country,
            "date_utc": self.date_utc,
            "impact": self.impact,
            "forecast": self.forecast,
            "previous": self.previous,
            "time_ist": self.time_ist,
            "time_dual": self.time_dual_str,
        }

    def __getitem__(self, key: str) -> Any:
        if key == "time_utc":
            return self.date_utc
        if key == "time_ist":
            return self.time_ist
        if key == "time_dual":
            return self.time_dual_str
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except AttributeError:
            return default


class NewsEngine:
    def __init__(
        self,
        feed_url: str = DEFAULT_FEED_URL,
        cache_dir: Optional[str] = None,
        cache_expiry_hours: float = 4.0,
        monitored_currencies: Optional[List[str]] = None,
        impact_levels: Optional[List[str]] = None,
        blackout_minutes: Optional[int] = None,
        window_minutes: int = 15,
    ):
        self.feed_url = feed_url
        if cache_dir is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.cache_dir = os.path.join(base_dir, "logs")
        else:
            self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

        self.cache_file = os.path.join(self.cache_dir, "news_calendar_cache.json")
        self.cache_expiry_hours = cache_expiry_hours
        self.monitored_currencies = monitored_currencies or ["USD"]
        self.impact_levels = impact_levels or ["High"]
        self.window_minutes = blackout_minutes if blackout_minutes is not None else window_minutes

        self.events: List[EconomicEvent] = []
        self.last_fetch_time: Optional[datetime] = None

        # Load initial cache or fetch
        self.load_or_refresh_calendar()

    def _parse_iso_date(self, date_str: str) -> Optional[datetime]:
        """
        Parses ISO 8601 date string (e.g. '2026-09-04T08:30:00-04:00') into UTC datetime.
        Handles Python 3.10+ fromisoformat with offset.
        """
        try:
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        except Exception:
            return None

    def load_or_refresh_calendar(self, force_refresh: bool = False) -> bool:
        """Loads calendar from local cache if fresh, otherwise fetches from feed."""
        now_utc = datetime.now(timezone.utc)

        # Check if cache is fresh
        if not force_refresh and os.path.exists(self.cache_file):
            try:
                mod_time = datetime.fromtimestamp(os.path.getmtime(self.cache_file), tz=timezone.utc)
                if (now_utc - mod_time).total_seconds() < (self.cache_expiry_hours * 3600.0):
                    with open(self.cache_file, "r", encoding="utf-8") as f:
                        raw_data = json.load(f)
                    self._process_raw_events(raw_data)
                    self.last_fetch_time = mod_time
                    return True
            except Exception as e:
                print(f"[NewsEngine] Warn: Failed loading local cache: {e}. Refreshing from web.")

        # Fetch fresh data from web
        return self.fetch_fresh_calendar()

    def fetch_fresh_calendar(self) -> bool:
        """Fetches fresh JSON from FairEconomy Forex Factory feed and saves to disk cache."""
        try:
            req = urllib.request.Request(
                self.feed_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) InstitutionalDCCBot/2.0"},
            )
            try:
                with urllib.request.urlopen(req, timeout=12, context=_get_ssl_context()) as response:
                    if response.status == 200:
                        content = response.read().decode("utf-8")
                        raw_data = json.loads(content)
                        self._process_raw_events(raw_data)

                        # Save to local disk cache
                        with open(self.cache_file, "w", encoding="utf-8") as f:
                            json.dump(raw_data, f, indent=2)

                        self.last_fetch_time = datetime.now(timezone.utc)
                        print(f"[NewsEngine] Calendar refreshed! Loaded {len(self.events)} high-impact events.")
                        return True
            except Exception as e:
                err_str = str(e)
                if "CERTIFICATE_VERIFY_FAILED" in err_str or "certificate verify failed" in err_str.lower():
                    unverified_ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=12, context=unverified_ctx) as response:
                        if response.status == 200:
                            content = response.read().decode("utf-8")
                            raw_data = json.loads(content)
                            self._process_raw_events(raw_data)
                            with open(self.cache_file, "w", encoding="utf-8") as f:
                                json.dump(raw_data, f, indent=2)
                            self.last_fetch_time = datetime.now(timezone.utc)
                            print(f"[NewsEngine] Calendar refreshed (via fallback SSL)! Loaded {len(self.events)} high-impact events.")
                            return True
                raise e
        except Exception as e:
            print(f"[NewsEngine] Warn: Failed to fetch calendar from web: {e}")
            # Fallback to existing disk cache if available
            if os.path.exists(self.cache_file):
                try:
                    with open(self.cache_file, "r", encoding="utf-8") as f:
                        raw_data = json.load(f)
                    self._process_raw_events(raw_data)
                    print(f"[NewsEngine] Using existing offline disk cache ({len(self.events)} events).")
                    return True
                except Exception:
                    pass
        return False

    def _process_raw_events(self, raw_data: List[Dict]):
        """Filters and parses high-impact events for monitored currencies."""
        parsed: List[EconomicEvent] = []
        for item in raw_data:
            country = item.get("country", "").upper()
            impact = item.get("impact", "")
            if country in self.monitored_currencies and impact in self.impact_levels:
                date_str = item.get("date", "")
                dt_utc = self._parse_iso_date(date_str)
                if dt_utc:
                    parsed.append(EconomicEvent(
                        title=item.get("title", "Economic Event"),
                        country=country,
                        date_utc=dt_utc,
                        impact=impact,
                        forecast=item.get("forecast", ""),
                        previous=item.get("previous", ""),
                    ))

        # Sort chronologically
        parsed.sort(key=lambda x: x.date_utc)
        self.events = parsed

    def get_active_news_shield(
        self,
        now_utc: Optional[datetime] = None,
        window_minutes: Optional[int] = None,
    ) -> Optional[Dict]:
        """
        Checks if the current time falls within [event - window, event + window]
        of any monitored high-impact economic news event.
        Returns a dictionary with shield details if active, else None.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        win_m = window_minutes if window_minutes is not None else self.window_minutes
        win_delta = timedelta(minutes=win_m)

        for ev in self.events:
            ev_time = ev.date_utc
            shield_start = ev_time - win_delta
            shield_end = ev_time + win_delta

            if shield_start <= now_utc <= shield_end:
                sec_to_event = (ev_time - now_utc).total_seconds()
                sec_remaining = (shield_end - now_utc).total_seconds()
                ev_time_ist = ev_time.astimezone(TZ_IST)
                shield_start_ist = shield_start.astimezone(TZ_IST)
                shield_end_ist = shield_end.astimezone(TZ_IST)

                return {
                    "is_active": True,
                    "title": ev.title,
                    "country": ev.country,
                    "impact": ev.impact,
                    "event_time_utc": ev_time,
                    "event_time_ist": ev_time_ist,
                    "event_time_str": ev_time.strftime("%H:%M"),
                    "event_time_ist_str": ev_time_ist.strftime("%H:%M"),
                    "event_time_dual": f"{ev_time.strftime('%H:%M')} UTC ({ev_time_ist.strftime('%H:%M')} IST)",
                    "shield_start_utc": shield_start,
                    "shield_start_ist": shield_start_ist,
                    "shield_end_utc": shield_end,
                    "shield_end_ist": shield_end_ist,
                    "blackout_start_str": shield_start.strftime("%H:%M"),
                    "blackout_start_ist_str": shield_start_ist.strftime("%H:%M"),
                    "blackout_start_dual": f"{shield_start.strftime('%H:%M')} UTC ({shield_start_ist.strftime('%H:%M')} IST)",
                    "resume_time_str": shield_end.strftime("%H:%M"),
                    "resume_time_ist_str": shield_end_ist.strftime("%H:%M"),
                    "resume_time_dual": f"{shield_end.strftime('%H:%M')} UTC ({shield_end_ist.strftime('%H:%M')} IST)",
                    "seconds_remaining": max(0.0, sec_remaining),
                    "minutes_to_event": round(sec_to_event / 60.0, 1),
                    "minutes_remaining": max(0.1, round(sec_remaining / 60.0, 1)),
                    "forecast": ev.forecast,
                    "previous": ev.previous,
                }

        return None

    def get_upcoming_events(
        self,
        now_utc: Optional[datetime] = None,
        hours_ahead: int = 24,
    ) -> List[EconomicEvent]:
        """Returns high-impact events occurring within the next hours_ahead window."""
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        end_window = now_utc + timedelta(hours=hours_ahead)
        upcoming = []
        for ev in self.events:
            if now_utc <= ev.date_utc <= end_window:
                upcoming.append(ev)
        return upcoming

    def get_todays_events(self, now_utc: Optional[datetime] = None) -> List[EconomicEvent]:
        """Returns all high-impact events scheduled for the current UTC date."""
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        today = now_utc.date()
        return [ev for ev in self.events if ev.date_utc.date() == today]
