import os
import datetime
import pickle
import logging
from typing import List, Dict, Optional, Union
try:
    from googleapiclient.discovery import build
except ImportError:
    build = None

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
except ImportError:
    InstalledAppFlow = None
    Request = None
from ai_assistant.config import get_data_dir

# Scopes required for the API
SCOPES = ['https://www.googleapis.com/auth/calendar']

logger = logging.getLogger(__name__)

class CalendarManager:
    """
    Manages interactions with the Google Calendar API.
    Handles authentication and provides methods to read and write events.
    """
    def __init__(self):
        self.creds = None
        self.service = None
        self._deps_available = build is not None and InstalledAppFlow is not None and Request is not None
        self.data_dir = get_data_dir()
        self.credentials_path = os.path.join(self.data_dir, 'credentials.json')
        self.token_path = os.path.join(self.data_dir, 'token.json')

        # Fallback to root if not in data dir (for user convenience)
        if not os.path.exists(self.credentials_path):
            root_creds = 'credentials.json'
            if os.path.exists(root_creds):
                self.credentials_path = root_creds

    def _load_cached_credentials(self):
        """Load cached OAuth credentials from token path, returning None on failure."""
        try:
            with open(self.token_path, 'rb') as token:
                return pickle.load(token)
        except FileNotFoundError:
            logger.warning("token.json was reported as present but could not be opened.")
        except Exception as e:
            logger.error(f"Error loading token.json: {e}")
        return None

    def authenticate(self) -> bool:
        """
        Authenticates the user using OAuth2.
        Loads token.json if available, otherwise initiates a local server flow
        using credentials.json.
        """
        self.creds = None
        # The file token.json stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if os.path.exists(self.token_path):
            self.creds = self._load_cached_credentials()

        # If there are no (valid) credentials available, let the user log in.
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                try:
                    self.creds.refresh(Request())
                except Exception as e:
                    logger.error(f"Error refreshing token: {e}")
                    self.creds = None

            if not self.creds:
                if not os.path.exists(self.credentials_path):
                    logger.error(f"credentials.json not found at {self.credentials_path}. Cannot authenticate.")
                    return False

                try:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.credentials_path, SCOPES)
                    # This will open a browser window for authentication
                    self.creds = flow.run_local_server(port=0)
                except Exception as e:
                     logger.error(f"Error during OAuth flow: {e}")
                     return False

            # Save the credentials for the next run
            try:
                with open(self.token_path, 'wb') as token:
                    pickle.dump(self.creds, token)
            except Exception as e:
                logger.error(f"Error saving token.json: {e}")

        try:
            self.service = build('calendar', 'v3', credentials=self.creds)
            return True
        except Exception as e:
            logger.error(f"Error building service: {e}")
            return False

    def list_upcoming_events(self, max_results: int = 10) -> str:
        """
        Returns a formatted string of upcoming events.
        Uses local system time for 'now'.
        """
        if not self.service:
            if not self.authenticate():
                return "Error: Could not authenticate with Google Calendar."

        try:
            # Use local time, but API expects ISO formatted string with offset or Z.
            # Using 'Z' means UTC. If we want local, we should probably just send UTC time for 'now'
            # but getting that 'now' correctly.
            # actually timeMin expects an RFC3339 timestamp.
            # datetime.datetime.utcnow().isoformat() + 'Z' is correct for "current time in UTC".
            # The API will return events relative to that.
            # But the OUTPUT should probably be friendly.

            now = datetime.datetime.now(datetime.timezone.utc).isoformat()

            events_result = self.service.events().list(calendarId='primary', timeMin=now,
                                                    maxResults=max_results, singleEvents=True,
                                                    orderBy='startTime').execute()
            events = events_result.get('items', [])

            if not events:
                return "No upcoming events found."

            output = "Upcoming Events:\n"
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                summary = event.get('summary', 'No Title')
                output += f"- {start}: {summary}\n"

            return output

        except Exception as e:
            logger.error(f"Error listing events: {e}")
            return f"Error listing events: {str(e)}"

    def create_event(self, summary: str, start_time: str, duration_mins: int = 60, description: str = "") -> str:
        """
        Creates a new event.
        start_time should be in ISO format (e.g., '2023-10-27T10:00:00').
        If no timezone is specified in the string, local system time is assumed.
        """
        if not self.service:
            if not self.authenticate():
                return "Error: Could not authenticate with Google Calendar."

        try:
            # Parse start time
            try:
                start_dt = datetime.datetime.fromisoformat(start_time)
            except ValueError:
                 return f"Error: Invalid date format '{start_time}'. Please use ISO format (YYYY-MM-DDTHH:MM:SS)."

            # If tzinfo is missing, assume local system time implicitly by NOT adding 'Z' or converting to UTC forcibly
            # and letting Google Calendar interpret it as "floating" time or handling it via the calendar's timezone setting.
            # Better: Ask API to use the primary calendar's timezone.

            # Fetch primary calendar timezone
            calendar = self.service.calendars().get(calendarId='primary').execute()
            calendar_tz = calendar.get('timeZone', 'UTC')

            # Calculate end time
            end_dt = start_dt + datetime.timedelta(minutes=duration_mins)

            event_body = {
                'summary': summary,
                'description': description,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': calendar_tz,
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': calendar_tz,
                },
            }

            event = self.service.events().insert(calendarId='primary', body=event_body).execute()
            return f"Event created: {event.get('htmlLink')}"

        except Exception as e:
            logger.error(f"Error creating event: {e}")
            return f"Error creating event: {str(e)}"

    def get_day_agenda(self, date_str: str) -> str:
        """
        Returns all events for a specific day.
        date_str should be 'today', 'tomorrow', or YYYY-MM-DD.
        Calculates the day based on the Local System Time, not UTC.
        """
        if not self.service:
            if not self.authenticate():
                return "Error: Could not authenticate with Google Calendar."

        try:
            # Use local date
            today = datetime.datetime.now().date()

            if date_str.lower() == 'today':
                target_date = today
            elif date_str.lower() == 'tomorrow':
                target_date = today + datetime.timedelta(days=1)
            else:
                try:
                    target_date = datetime.date.fromisoformat(date_str)
                except ValueError:
                    return f"Error: Invalid date format '{date_str}'. Use 'today', 'tomorrow', or YYYY-MM-DD."

            # Fetch primary calendar timezone to construct correct timeMin/timeMax
            calendar = self.service.calendars().get(calendarId='primary').execute()
            calendar_tz_str = calendar.get('timeZone', 'UTC')

            # We need timezone aware datetimes for the API query
            # But python's datetime.combine creates naive datetimes by default.
            # We can use str formatting to pass to API with timezone info, or use the timezone for query.

            # Actually, Google API `timeMin` and `timeMax` must be RFC3339 timestamp with mandatory time zone offset, e.g., 2011-06-03T10:00:00-07:00
            # If we don't know the offset of the calendar's timezone easily without pytz (which might not be installed),
            # we can ask for full day by relying on 'singleEvents=True' and just covering the 24h period roughly or using the user's local machine time zone if it matches.

            # Safest approach without pytz:
            # 1. Get local machine's current offset or just use local time and formatted with offset if possible.
            # 2. Or, since we want "User's Agenda", assuming the code runs on User's machine (Local Assistant), `datetime.now().astimezone()` gives local time with offset.

            target_dt_start = datetime.datetime.combine(target_date, datetime.time.min).replace(tzinfo=None)
            target_dt_end = datetime.datetime.combine(target_date, datetime.time.max).replace(tzinfo=None)

            # Convert these "local" times to aware times using the system's local timezone
            start_aware = target_dt_start.astimezone()
            end_aware = target_dt_end.astimezone()

            events_result = self.service.events().list(calendarId='primary',
                                                    timeMin=start_aware.isoformat(),
                                                    timeMax=end_aware.isoformat(),
                                                    singleEvents=True,
                                                    orderBy='startTime').execute()
            events = events_result.get('items', [])

            if not events:
                return f"No events found for {target_date}."

            output = f"Agenda for {target_date}:\n"
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                summary = event.get('summary', 'No Title')
                output += f"- {start}: {summary}\n"

            return output

        except Exception as e:
            logger.error(f"Error getting agenda: {e}")
            return f"Error getting agenda: {str(e)}"
