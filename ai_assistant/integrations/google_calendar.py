import os
import datetime
import pickle
import logging
from typing import List, Dict, Optional, Union
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
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
        self.data_dir = get_data_dir()
        self.credentials_path = os.path.join(self.data_dir, 'credentials.json')
        self.token_path = os.path.join(self.data_dir, 'token.json')

        # Fallback to root if not in data dir (for user convenience)
        if not os.path.exists(self.credentials_path):
            root_creds = 'credentials.json'
            if os.path.exists(root_creds):
                self.credentials_path = root_creds

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
            try:
                with open(self.token_path, 'rb') as token:
                    self.creds = pickle.load(token)
            except Exception as e:
                logger.error(f"Error loading token.json: {e}")

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
        """
        if not self.service:
            if not self.authenticate():
                return "Error: Could not authenticate with Google Calendar."

        try:
            now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
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
        start_time should be in ISO format (e.g., '2023-10-27T10:00:00') or recognizable by fromisoformat.
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

            end_dt = start_dt + datetime.timedelta(minutes=duration_mins)

            event_body = {
                'summary': summary,
                'description': description,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'UTC', # Assuming UTC for simplicity, or we could fetch local
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'UTC',
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
        """
        if not self.service:
            if not self.authenticate():
                return "Error: Could not authenticate with Google Calendar."

        try:
            today = datetime.datetime.utcnow().date()

            if date_str.lower() == 'today':
                target_date = today
            elif date_str.lower() == 'tomorrow':
                target_date = today + datetime.timedelta(days=1)
            else:
                try:
                    target_date = datetime.date.fromisoformat(date_str)
                except ValueError:
                    return f"Error: Invalid date format '{date_str}'. Use 'today', 'tomorrow', or YYYY-MM-DD."

            # Start of day
            time_min = datetime.datetime.combine(target_date, datetime.time.min).isoformat() + 'Z'
            # End of day
            time_max = datetime.datetime.combine(target_date, datetime.time.max).isoformat() + 'Z'

            events_result = self.service.events().list(calendarId='primary', timeMin=time_min,
                                                    timeMax=time_max, singleEvents=True,
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
