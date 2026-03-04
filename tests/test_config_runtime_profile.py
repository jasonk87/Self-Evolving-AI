import importlib
import sys

import pytest


MODULE = "ai_assistant.config"


def _reload_config(monkeypatch, app_env=None, secret_key=None, debug_mode=None, verbose_mode=None):
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)

    if secret_key is None:
        monkeypatch.delenv("SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("SECRET_KEY", secret_key)

    if debug_mode is None:
        monkeypatch.delenv("DEBUG_MODE", raising=False)
    else:
        monkeypatch.setenv("DEBUG_MODE", debug_mode)

    if verbose_mode is None:
        monkeypatch.delenv("VERBOSE_LLM_LOGGING", raising=False)
    else:
        monkeypatch.setenv("VERBOSE_LLM_LOGGING", verbose_mode)

    if MODULE in sys.modules:
        del sys.modules[MODULE]
    return importlib.import_module(MODULE)


def test_development_defaults_enable_debug_and_verbose(monkeypatch):
    cfg = _reload_config(monkeypatch, app_env="development")
    assert cfg.IS_PRODUCTION is False
    assert cfg.DEBUG_MODE is True
    assert cfg.VERBOSE_LLM_LOGGING is True


def test_production_defaults_disable_debug_and_verbose(monkeypatch):
    cfg = _reload_config(monkeypatch, app_env="production", secret_key="super-secure")
    assert cfg.IS_PRODUCTION is True
    assert cfg.DEBUG_MODE is False
    assert cfg.VERBOSE_LLM_LOGGING is False


def test_production_requires_secure_secret_key(monkeypatch):
    with pytest.raises(RuntimeError, match="SECRET_KEY must be set"):
        _reload_config(monkeypatch, app_env="production", secret_key=None)


def test_env_flags_override_development_defaults(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        app_env="development",
        debug_mode="false",
        verbose_mode="0",
    )
    assert cfg.DEBUG_MODE is False
    assert cfg.VERBOSE_LLM_LOGGING is False


def test_env_flags_override_production_defaults(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        app_env="production",
        secret_key="super-secure",
        debug_mode="true",
        verbose_mode="yes",
    )
    assert cfg.DEBUG_MODE is True
    assert cfg.VERBOSE_LLM_LOGGING is True
