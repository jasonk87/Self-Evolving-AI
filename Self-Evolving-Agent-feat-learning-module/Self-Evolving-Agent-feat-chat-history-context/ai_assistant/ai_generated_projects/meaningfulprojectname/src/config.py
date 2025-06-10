import os

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
api_keys_str = os.environ.get('API_KEYS', '')
API_KEYS = api_keys_str.split(',') if api_keys_str else []
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

def load_config():
    return {
        'DATABASE_URL': DATABASE_URL,
        'API_KEYS': API_KEYS,
        'LOG_LEVEL': LOG_LEVEL
    }