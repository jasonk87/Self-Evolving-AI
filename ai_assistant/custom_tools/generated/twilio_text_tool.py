from typing import Optional
import os
from typing import TYPE_CHECKING, Optional
if TYPE_CHECKING:
    from ai_assistant.core.action_executor import ActionExecutor

async def send_text_message(action_executor: 'ActionExecutor', recipient: str, message: str, twilio_account_sid: Optional[str]=None, twilio_auth_token: Optional[str]=None, twilio_phone_number: Optional[str]=None) -> str:
    """
    Sends a text message to the specified recipient using Twilio.

    Args:
        action_executor: The action executor.
        recipient (str): The recipient's phone number (e.g., "+15551234567").
        message (str): The text message to send.
        twilio_account_sid (Optional[str]): Your Twilio Account SID. If None, it will attempt to read from the TWILIO_ACCOUNT_SID environment variable.
        twilio_auth_token (Optional[str]): Your Twilio Auth Token. If None, it will attempt to read from the TWILIO_AUTH_TOKEN environment variable.
        twilio_phone_number (Optional[str]): Your Twilio phone number. If None, it will attempt to read from the TWILIO_PHONE_NUMBER environment variable.

    Returns:
        str: A success message if the text was sent, or an error message if there was a problem.
    """
    try:
        account_sid = twilio_account_sid or os.environ.get('TWILIO_ACCOUNT_SID')
        auth_token = twilio_auth_token or os.environ.get('TWILIO_AUTH_TOKEN')
        phone_number = twilio_phone_number or os.environ.get('TWILIO_PHONE_NUMBER')
        if not account_sid:
            return 'Error: Twilio Account SID not provided and TWILIO_ACCOUNT_SID environment variable not set.'
        if not auth_token:
            return 'Error: Twilio Auth Token not provided and TWILIO_AUTH_TOKEN environment variable not set.'
        if not phone_number:
            return 'Error: Twilio Phone Number not provided and TWILIO_PHONE_NUMBER environment variable not set.'
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        message_obj = client.messages.create(to=recipient, from_=phone_number, body=message)
        return f'Text message sent successfully! Message SID: {message_obj.sid}'
    except ImportError:
        return 'Error: Twilio library not installed. Please install it using `pip install twilio`.'
    except Exception as e:
        return f'Error sending text message: {e}'