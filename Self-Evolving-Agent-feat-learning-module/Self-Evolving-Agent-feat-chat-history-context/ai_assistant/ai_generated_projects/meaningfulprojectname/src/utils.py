import logging
import datetime
import config

def validate_integer(value, min_val=None, max_val=None):
    if not isinstance(value, int):
        raise ValueError(f"Expected integer, got {type(value)}")
    if min_val is not None and value < min_val:
        raise ValueError(f"Value {value} is below minimum {min_val}")
    if max_val is not None and value > max_val:
        raise ValueError(f"Value {value} exceeds maximum {max_val}")

def validate_string(value, min_length=None, max_length=None):
    if not isinstance(value, str):
        raise ValueError(f"Expected string, got {type(value)}")
    if min_length is not None and len(value) < min_length:
        raise ValueError(f"String length {len(value)} is below minimum {min_length}")
    if max_length is not None and len(value) > max_length:
        raise ValueError(f"String length {len(value)} exceeds maximum {max_length}")

def log_error(message, error=None):
    logging.error(message)
    if error:
        logging.error("Error details: %s", error)

def get_current_time(format_str="%Y-%m-%d %H:%M:%S"):
    return datetime.datetime.now().strftime(format_str)