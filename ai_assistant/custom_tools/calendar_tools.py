from ai_assistant.integrations.google_calendar import CalendarManager

# Global instance
_calendar_manager = None

def _get_manager():
    global _calendar_manager
    if _calendar_manager is None:
        _calendar_manager = CalendarManager()
    return _calendar_manager

def check_calendar(date_str: str) -> str:
    """
    Checks the user's Google Calendar schedule for a specific date.

    Args:
        date_str (str): The date to check. Accepts 'today', 'tomorrow', or 'YYYY-MM-DD'.

    Returns:
        str: A list of events for that day, or a message if no events found.
    """
    if not isinstance(date_str, str) or not date_str.strip():
        return "Error: Date must be 'today', 'tomorrow', or YYYY-MM-DD."
    normalized_date = date_str.strip()
    manager = _get_manager()
    return manager.get_day_agenda(normalized_date)

def schedule_event(summary: str, datetime_str: str, duration_mins: int = 60, description: str = "") -> str:
    """
    Schedules a new event on the user's Google Calendar.

    Args:
        summary (str): The title of the event.
        datetime_str (str): The start time in ISO format (YYYY-MM-DDTHH:MM:SS).
        duration_mins (int, optional): Duration in minutes. Defaults to 60.
        description (str, optional): A description for the event.

    Returns:
        str: Confirmation message with link or error details.
    """
    manager = _get_manager()
    return manager.create_event(summary, datetime_str, duration_mins, description)

def list_upcoming_events(max_results: int = 10) -> str:
    """
    Lists the next upcoming events on the user's calendar.

    Args:
        max_results (int, optional): Maximum number of events to list. Defaults to 10.

    Returns:
        str: A list of upcoming events.
    """
    manager = _get_manager()
    return manager.list_upcoming_events(max_results)
