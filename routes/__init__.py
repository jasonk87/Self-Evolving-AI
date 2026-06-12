# routes/__init__.py
from flask import Blueprint

# Define Blueprints
api_bp = Blueprint('api', __name__, url_prefix='/api')
views_bp = Blueprint('views', __name__)

# Import routes to register them with blueprints

from . import files, projects, chat, memory, system, views, approvals, live  # noqa: F401
