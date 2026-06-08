import pytest
import logging
from io import StringIO
import json
import uuid

from opentelemetry import trace
from ai_assistant.core.logging_config import setup_logging_and_tracing, correlation_id_var
import ai_assistant.config as config
from pythonjsonlogger import jsonlogger

def test_tracing_disabled():
    orig_tracing = config.ENABLE_TRACING
    config.ENABLE_TRACING = False

    root_logger = logging.getLogger()
    log_stream = StringIO()
    test_handler = logging.StreamHandler(log_stream)

    try:
        root_logger.addHandler(test_handler)
        root_logger.setLevel(logging.INFO)

        setup_logging_and_tracing()

        logger = logging.getLogger("test_logger")
        logger.setLevel(logging.INFO)
        logger.info("Test disabled tracing")

        log_output = log_stream.getvalue()
        assert "Test disabled tracing" in log_output
        assert not log_output.strip().startswith("{")
    finally:
        root_logger.removeHandler(test_handler)
        config.ENABLE_TRACING = orig_tracing

def test_tracing_enabled_json_logs():
    orig_tracing = config.ENABLE_TRACING
    config.ENABLE_TRACING = True

    root_logger = logging.getLogger()
    log_stream = StringIO()
    test_handler = logging.StreamHandler(log_stream)

    try:
        root_logger.addHandler(test_handler)

        # Calling setup will dynamically convert StreamHandlers to JSON + attach filters
        setup_logging_and_tracing()

        root_logger.setLevel(logging.INFO)
        logger = logging.getLogger("test_logger")
        logger.setLevel(logging.INFO)

        correlation_id = str(uuid.uuid4())
        token = correlation_id_var.set(correlation_id)

        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("test_span"):
            logger.info("Test enabled tracing")

        correlation_id_var.reset(token)

        log_output = log_stream.getvalue()
        assert log_output.strip() != ""

        lines = [line for line in log_output.strip().split('\n') if line]
        last_line = lines[-1]

        print(f"RAW LOG: {last_line}")
        log_json = json.loads(last_line)

        assert "correlation_id" in log_json
        assert log_json["correlation_id"] == correlation_id
        assert "trace_id" in log_json
        assert "span_id" in log_json
        assert log_json["message"] == "Test enabled tracing"

    finally:
        root_logger.removeHandler(test_handler)
        config.ENABLE_TRACING = orig_tracing
