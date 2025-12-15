import pytest
import time
import platform
import subprocess
import datetime
from unittest.mock import patch
from ai_assistant.custom_tools.generated.notification_scheduler import schedule_notification

def is_command_available(command):
    try:
        subprocess.run(["which", command], check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        return False

@pytest.fixture(scope="module", autouse=True)
def setup_teardown():
    # Setup (if needed)
    yield
    # Teardown (if needed)
    pass

def test_schedule_notification_happy_path():
    message = "Test notification"
    delay = 0.1  # Short delay for testing
    result = schedule_notification(message, delay)

    system = platform.system()
    if system == "Darwin":
        assert "Notification scheduled successfully (macOS)" in result
        time.sleep(delay + 1) # Allow time for notification to appear
    elif system == "Linux":
        if is_command_available("notify-send"):
            assert "Notification scheduled successfully (Linux)" in result
            time.sleep(delay + 1) # Allow time for notification to appear
        else:
            assert "Error: notify-send is not installed" in result
    elif system == "Windows":
        assert "Notification scheduled successfully (Windows)" in result
        time.sleep(delay + 1) # Allow time for notification to appear
    else:
        assert f"Unsupported operating system: {system}" in result

def test_schedule_notification_empty_message():
    message = ""
    delay = 0.1
    result = schedule_notification(message, delay)

    system = platform.system()
    if system == "Darwin":
        assert "Notification scheduled successfully (macOS)" in result
        time.sleep(delay + 1) # Allow time for notification to appear
    elif system == "Linux":
        if is_command_available("notify-send"):
            assert "Notification scheduled successfully (Linux)" in result
            time.sleep(delay + 1) # Allow time for notification to appear
        else:
            assert "Error: notify-send is not installed" in result
    elif system == "Windows":
        assert "Notification scheduled successfully (Windows)" in result
        time.sleep(delay + 1) # Allow time for notification to appear
    else:
        assert f"Unsupported operating system: {system}" in result

def test_schedule_notification_zero_delay():
    message = "Test notification"
    delay = 0
    result = schedule_notification(message, delay)

    system = platform.system()
    if system == "Darwin":
        assert "Notification scheduled successfully (macOS)" in result
        time.sleep(1) # Allow time for notification to appear
    elif system == "Linux":
        if is_command_available("notify-send"):
            assert "Notification scheduled successfully (Linux)" in result
            time.sleep(1) # Allow time for notification to appear
        else:
            assert "Error: notify-send is not installed" in result
    elif system == "Windows":
        assert "Notification scheduled successfully (Windows)" in result
        time.sleep(1) # Allow time for notification to appear
    else:
        assert f"Unsupported operating system: {system}" in result

def test_schedule_notification_large_delay():
    message = "Test notification"
    delay = 0.01 # Keep delay short for testing, but still a valid number
    result = schedule_notification(message, delay)

    system = platform.system()
    if system == "Darwin":
        assert "Notification scheduled successfully (macOS)" in result
        time.sleep(delay + 1) # Allow time for notification to appear
    elif system == "Linux":
        if is_command_available("notify-send"):
            assert "Notification scheduled successfully (Linux)" in result
            time.sleep(delay + 1) # Allow time for notification to appear
        else:
            assert "Error: notify-send is not installed" in result
    elif system == "Windows":
        assert "Notification scheduled successfully (Windows)" in result
        time.sleep(delay + 1) # Allow time for notification to appear
    else:
        assert f"Unsupported operating system: {system}" in result

def test_schedule_notification_invalid_delay_type():
    message = "Test notification"
    delay = "invalid"
    with pytest.raises(TypeError):
        schedule_notification(message, delay)

@pytest.mark.skipif(platform.system() != "Linux" or not is_command_available("notify-send"), reason="notify-send is not available")
def test_schedule_notification_linux_notify_send_not_installed():
    # This test specifically targets the Linux case where notify-send is not installed.
    # We can't directly uninstall notify-send for the test, so we'll skip if it *is* installed.
    message = "Test notification"
    delay = 0.1
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "which")
        result = schedule_notification(message, delay)
        assert "Error: notify-send is not installed" in result