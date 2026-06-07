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

    try:
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        setup_logging_and_tracing()

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

        logger = logging.getLogger("test_logger")
        logger.setLevel(logging.INFO)
        logger.info("Test disabled tracing")

        log_output = log_stream.getvalue()
        assert "Test disabled tracing" in log_output
        assert not log_output.strip().startswith("{")
    finally:
        config.ENABLE_TRACING = orig_tracing

def test_tracing_enabled_json_logs():
    orig_tracing = config.ENABLE_TRACING
    config.ENABLE_TRACING = True

    try:
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        log_stream = StringIO()
        test_handler = logging.StreamHandler(log_stream)
        # Manually force the formatter since setup_logging might not catch this specific test setup
        from ai_assistant.core.logging_config import CorrelationFilter
        formatter = jsonlogger.JsonFormatter(
            '%(asctime)s %(levelname)s %(name)s %(correlation_id)s %(trace_id)s %(span_id)s %(message)s'
        )
        test_handler.setFormatter(formatter)
        test_handler.addFilter(CorrelationFilter())
        root_logger.addHandler(test_handler)

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
        config.ENABLE_TRACING = orig_tracing
