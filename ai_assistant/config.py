# ai_assistant/config.py

# Default model to be used by the Ollama client if no specific model is requested for a task.
# Options: "gemini", "ollama"
import importlib
import importlib.util
import os

LLM_PROVIDER = "gemini" 

DEFAULT_MODEL = "gemini-2.0-flash"  # Switched to stable 2.0 model

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(project_root, '.env')

def _load_env_file(path: str) -> None:
    """Load environment values from a .env file when python-dotenv is available."""
    dotenv_spec = importlib.util.find_spec("dotenv")
    if dotenv_spec is None:
        return

    dotenv_module = importlib.import_module("dotenv")
    load_dotenv = getattr(dotenv_module, "load_dotenv", None)
    if callable(load_dotenv):
        load_dotenv(path)

_load_env_file(env_path)
from typing import Optional, Dict, List

# Runtime environment profile
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV in {"prod", "production"}


def _env_flag(name: str, default: bool) -> bool:
    """Parse boolean-like environment flags with a safe default."""
    raw = os.environ.get(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default

def _get_api_key() -> str:
    """Retrieves the Google API Key from config or environment."""
    # This function is now exposed for scripts
    if GOOGLE_API_KEY:
        return GOOGLE_API_KEY
    return os.environ.get("GOOGLE_API_KEY", "")

# Define models that support native thinking
THINKING_SUPPORTED_MODELS: List[str] = [
    "qwen3:latest",
    "deepseek-r1:latest",
    "qwen3:8B",
    "gemini-2.0-flash-exp",
    "gemini-2.0-flash"
]

# Enable or disable thinking capability globally (overrides per-model settings)
ENABLE_THINKING = True  # Set to False to disable thinking output globally

# Chain of thought settings for models that don't support native thinking
ENABLE_CHAIN_OF_THOUGHT = True  # Enable chain of thought prompting for non-thinking models
DEFAULT_EXECUTION_MODE = "THINKING_PRO"  # Options: AUTO, DIRECT, FAST_REACT, THINKING_PRO
DEFAULT_TEMPERATURE_THINKING = 0.7  # Temperature for thinking phase
DEFAULT_TEMPERATURE_RESPONSE = 0.5  # Temperature for response phase (slightly lower for more focused responses)

# Thinking output configuration
THINKING_CONFIG = {
    "display": {
        "prefix": "[Thinking] ",
        "suffix": "...done thinking.",
        "plan_prefix": "[Action Plan] ",
        "step_prefix": "Step ",
        "max_steps": 5,
        "show_working": True,     # Show thinking steps in debug mode
        "show_in_release": False  # Never show thinking in release mode
    },
    "components": {
        "planner": "[Planner] ",
        "reviewer": "[Reviewer] ",
        "executor": "[Executor] ",
        "thinker": "[Thinker] "
    }
}

# Task-specific model configurations.
# This allows using different models for different capabilities (e.g., code generation, planning, reflection).
# If a task is not listed here, or if its value is None, the DEFAULT_MODEL will be used.
TASK_MODELS: Dict[str, Optional[str]] = {
    "code_generation": "gemini-2.0-flash",
    "planning": "gemini-2.0-flash",
    "reflection": "gemini-2.0-flash",
    "conversation_intelligence": "gemini-2.0-flash",
    "argument_population": "gemini-2.0-flash",
    "goal_preprocessing": "gemini-2.0-flash",
    "summarization": "gemini-2.0-flash",
    "reviewing": "gemini-2.0-flash",
    "fact_extraction": "gemini-2.0-flash",
    "tool_design": "gemini-2.0-flash",
    "tool_creation": "gemini-2.0-flash",
    "council_skeptic": "gemini-2.0-flash",
    "council_judge": "gemini-2.0-flash",
}

# Reasoning Strategies Configuration
# Maps specific tasks to a strategy: "RAW", "STANDARD" (SPLIT_BRAIN), or "PARALLEL".
# "RAW": Direct single call (Efficient).
# "STANDARD" / "UNIVERSAL_BICAMERAL": Split Brain (Think -> Act) (Robust).
# "PARALLEL": Parallel Thinking (3 passes + Merge) (Deepest).
REASONING_STRATEGIES: Dict[str, str] = {
    "planning": "RAW",
    "code_generation": "RAW",
    "conversation_intelligence": "RAW",
    "council_judge": "RAW",
    "reviewing": "RAW",
    "summarization": "RAW",
    "default": "RAW"
}

# Parallel Thinking Configuration
PARALLEL_THINKING_CONFIG = {
    "num_branches": 3,
    "merge_model": "gemini-2.0-flash-exp", # Using the experimental model for better reasoning
    "temperature_branches": 0.7,
    "temperature_merge": 0.2
}

# Number of recent conversational turns (user/AI exchanges) to include in LLM prompts for context
CONVERSATION_HISTORY_TURNS = 5

# Number of seconds to wait before re-executing the project plan.
PROJECT_EXECUTION_INTERVAL_SECONDS = 900  # 15 minutes

# Number of seconds to wait before running the background fact store curation.
FACT_CURATION_INTERVAL_SECONDS = 21600  # 6 hours

# Enable or disable the AI's ability to autonomously learn facts from conversation.
AUTONOMOUS_LEARNING_ENABLED = True # MODIFIED FOR SCENARIO 5

# --- Auto-Approval Configuration ---
# Wait time (in seconds) before the AI auto-executes pending requests for Self-Evolving activities.
# Default: 600 (10 minutes) - gives the user a short window, then the AI reviews and decides.
AUTO_APPROVE_DELAY_SECONDS = 600

# --- Fresh Start Configuration ---
# If True, the application should attempt to clear existing knowledge (context, memory, reflections, suggestions, learned facts etc.) on startup.
CLEAR_EXISTING_KNOWLEDGE_ON_STARTUP = False # Default to False to preserve data

# Debug mode flag (set in config file)
DEBUG_MODE = _env_flag("DEBUG_MODE", default=not IS_PRODUCTION)

# Verbose Logging for LLM (Prints full prompts and responses to console)
VERBOSE_LLM_LOGGING = _env_flag("VERBOSE_LLM_LOGGING", default=not IS_PRODUCTION)

# --- Google Custom Search API Configuration ---
# IMPORTANT: For security, it is recommended to set your GOOGLE_API_KEY and
# GOOGLE_CSE_ID as environment variables in your deployment environment.
# The application will try to load them from there.
# Example (in bash):
# export GOOGLE_API_KEY="your_actual_api_key"
# export GOOGLE_CSE_ID="your_actual_cse_id"
#
# Load Google API Key from environment variable GOOGLE_API_KEY
GOOGLE_API_KEY: Optional[str] = os.environ.get('GOOGLE_API_KEY')
# Load Google Custom Search Engine ID from environment variable GOOGLE_CSE_ID
GOOGLE_CSE_ID: Optional[str] = os.environ.get('GOOGLE_CSE_ID')

# --- ElevenLabs TTS Configuration ---
ELEVENLABS_API_KEY: Optional[str] = os.environ.get('ELEVENLABS_API_KEY')
ELEVENLABS_VOICE_ID = "vGWWh1bodhwwi4yHd6qZ" # Marcus (Deep, Authoritative) - Morgan Freeman style approximation
ELEVENLABS_MODEL_ID = "eleven_turbo_v2" # Low latency model
MW_TTS_ENABLED = True


# Rate Limiting Configuration
# Set to True to enable rate limiting for API calls (recommended for Free Tier)
# Set to False to disable rate limiting (recommended for Paid Tier)
ENABLE_RATE_LIMITING = True
GLOBAL_RATE_LIMITER = 10

# --- Security Configuration ---
# Safe Mode prevents execution of potentially dangerous terminal commands via the API.
# When True, strict allowlisting or user confirmation is required.
SAFE_MODE = True
# Key for Flask Session Security (Should be loaded from .env)
SECRET_KEY = os.environ.get('SECRET_KEY', 'default-insecure-secret-change-in-prod')

if IS_PRODUCTION and SECRET_KEY == 'default-insecure-secret-change-in-prod':
    raise RuntimeError('SECRET_KEY must be set to a secure value when APP_ENV is production.')



def is_debug_mode() -> bool:
    """Returns True if debug mode is enabled in config."""
    return DEBUG_MODE

# Name of the directory to store data files like learned facts, logs, etc.
DATA_DIR_NAME = "data"
# Subdirectory within ai_assistant package where the DATA_DIR_NAME will be located.
CORE_SUBDIR_FOR_DATA = "core"
# Define a subdirectory within the main data_dir for projects
PROJECTS_SUBDIR = "projects"

def get_data_dir() -> str:
    """
    Returns the absolute path to the data directory (ai_assistant/core/data).
    Creates the directory if it does not exist.
    """
    # Assumes config.py is in the 'ai_assistant' directory.
    # os.path.dirname(__file__) is the 'ai_assistant' directory path
    ai_assistant_package_dir = os.path.dirname(__file__)
    data_path = os.path.abspath(os.path.join(ai_assistant_package_dir, CORE_SUBDIR_FOR_DATA, DATA_DIR_NAME))
    os.makedirs(data_path, exist_ok=True)
    return data_path

def get_projects_dir() -> str:
    """
    Returns the absolute path to the directory for storing project-related files.
    Creates the directory if it does not exist.
    """
    base_data_dir = get_data_dir()
    projects_path = os.path.join(base_data_dir, PROJECTS_SUBDIR)
    os.makedirs(projects_path, exist_ok=True)
    return projects_path

# Function to get the model name for a specific task, falling back to the default.
def get_model_for_task(task_name: str) -> str:
    """
    Retrieves the configured model for a given task, or the default model if not specified.
    
    Args:
        task_name (str): The name of the task (e.g., "code_generation", "planning").
        
    Returns:
        str: The name of the Ollama model to use.
    """
    return TASK_MODELS.get(task_name, DEFAULT_MODEL) or DEFAULT_MODEL

if __name__ == '__main__':
    print("--- Testing Configuration ---")

    # Test 1: Get default model
    default_model_retrieved = get_model_for_task("some_undefined_task")
    print(f"Model for 'some_undefined_task': {default_model_retrieved} (Expected: {DEFAULT_MODEL})")
    assert default_model_retrieved == DEFAULT_MODEL

    # Test 2: Get model for a defined task
    code_gen_model_retrieved = get_model_for_task("code_generation")
    expected_code_gen_model = TASK_MODELS.get("code_generation", DEFAULT_MODEL)
    print(f"Model for 'code_generation': {code_gen_model_retrieved} (Expected: {expected_code_gen_model})")
    assert code_gen_model_retrieved == expected_code_gen_model

    # Test 3: Get model for a task defined to use default (if any, or add one for test)
    # For this test, let's assume "planning" uses the default or is explicitly set to it.
    planning_model_retrieved = get_model_for_task("planning")
    expected_planning_model = TASK_MODELS.get("planning", DEFAULT_MODEL) # Should be DEFAULT_MODEL if not overridden
    print(f"Model for 'planning': {planning_model_retrieved} (Expected: {expected_planning_model})")
    assert planning_model_retrieved == expected_planning_model
    
    # Test new config value
    print(f"Fact curation interval: {FACT_CURATION_INTERVAL_SECONDS} (Expected: 3600)")
    assert FACT_CURATION_INTERVAL_SECONDS == 3600

    # Test new config value for clearing knowledge
    print(f"Clear existing knowledge on startup: {CLEAR_EXISTING_KNOWLEDGE_ON_STARTUP} (Expected: False)")
    assert CLEAR_EXISTING_KNOWLEDGE_ON_STARTUP == False # Assuming default is False


    # Test 4: Task where model is explicitly None in TASK_MODELS (should fallback to DEFAULT_MODEL)
    # Add a temporary entry for this test case
    TASK_MODELS["test_task_with_none_model"] = None
    model_for_none_task = get_model_for_task("test_task_with_none_model")
    print(f"Model for 'test_task_with_none_model' (set to None): {model_for_none_task} (Expected: {DEFAULT_MODEL})")
    assert model_for_none_task == DEFAULT_MODEL
    del TASK_MODELS["test_task_with_none_model"] # Clean up

    # Test 5: Get model for the new "summarization" task
    summarization_model_retrieved = get_model_for_task("summarization")
    expected_summarization_model = TASK_MODELS.get("summarization", DEFAULT_MODEL)
    print(f"Model for 'summarization': {summarization_model_retrieved} (Expected: {expected_summarization_model})")
    assert summarization_model_retrieved == expected_summarization_model

    # Test 6: Get model for the new "reviewing" task
    reviewing_model_retrieved = get_model_for_task("reviewing")
    expected_reviewing_model = TASK_MODELS.get("reviewing", DEFAULT_MODEL)
    print(f"Model for 'reviewing': {reviewing_model_retrieved} (Expected: {expected_reviewing_model})")
    assert reviewing_model_retrieved == expected_reviewing_model

    # Test 7: Get data directory
    data_dir = get_data_dir()
    print(f"Data directory: {data_dir}")
    assert os.path.exists(data_dir)
    assert os.path.basename(data_dir) == DATA_DIR_NAME

    # Test 8: Get projects directory
    projects_dir = get_projects_dir()
    print(f"Projects directory: {projects_dir}")
    assert os.path.exists(projects_dir)
    assert os.path.basename(projects_dir) == PROJECTS_SUBDIR

    # Test 9: Check environment variable loading (these will be None if not set in test env)
    print(f"GOOGLE_API_KEY from env: {GOOGLE_API_KEY}")
    print(f"GOOGLE_CSE_ID from env: {GOOGLE_CSE_ID}")


    print("--- Configuration Tests Passed ---")

# --- Deep Research Configuration ---
DEEP_RESEARCH_MAX_URLS = 3
DEEP_RESEARCH_TIMEOUT = 60
GHOST_MODE = True  # If True, the browser will be visible (headless=False)
AUTO_WEB_PIP = True
REMINDER_CHECK_INTERVAL_SECONDS = 10
DREAM_INTERVAL_SECONDS = 86400
ENABLE_DREAM_MODE = False
BROWSER_SLOW_MO = 100  # Milliseconds to slow down operations in Ghost Mode


# =====================================================================
# TASK ROUTING & PLUGGABLE LLM ARCHITECTURE
# =====================================================================
DEFAULT_LLM_PROVIDER = "gemini"

TASK_PROFILES = {
    "chat": {
        "provider": "gemini",
        "model": "gemini-2.0-flash",
        "mode": "BICAMERAL",
        "endpoint": None
    },
    "coding": {
        "provider": "gemini",
        "model": "gemini-2.0-flash",
        "mode": "BICAMERAL",
        "endpoint": None
    },
    "background_dreamer": {
        "provider": "gemini",
        "model": "gemini-2.0-flash",
        "mode": "BICAMERAL",
        "endpoint": None
    },
    "local_agent": {
        "provider": "gemini",
        "model": "gemini-2.0-flash",
        "mode": "BICAMERAL",
        "endpoint": None
    }
}
