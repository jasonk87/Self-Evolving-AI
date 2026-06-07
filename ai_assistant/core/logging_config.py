import logging
import contextvars
from pythonjsonlogger import jsonlogger
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter, BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from ai_assistant.config import ENABLE_TRACING, OTEL_EXPORTER_OTLP_ENDPOINT

# ContextVar to store the correlation ID across async calls
correlation_id_var = contextvars.ContextVar('correlation_id', default=None)

class CorrelationFilter(logging.Filter):
    """
    Injects correlation_id, trace_id, and span_id into the log record.
    """
    def filter(self, record):
        correlation_id = correlation_id_var.get()
        if correlation_id:
            record.correlation_id = correlation_id

        span = trace.get_current_span()
        if span and span.is_recording():
            ctx = span.get_span_context()
            if ctx.is_valid:
                # Format exactly as conventional OpenTelemetry JSON logs expect
                record.trace_id = trace.format_trace_id(ctx.trace_id)
                record.span_id = trace.format_span_id(ctx.span_id)

        return True

def setup_logging_and_tracing():
    """
    Configures standard structured logging and sets up OpenTelemetry tracer.
    Respects ENABLE_TRACING config. Does not destructively clear all handlers.
    """
    if not ENABLE_TRACING:
        # Fallback to standard logging if tracing is disabled
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        return trace.get_tracer(__name__)

    if ENABLE_TRACING:
        # 1. Setup OpenTelemetry
        provider = TracerProvider()

        if OTEL_EXPORTER_OTLP_ENDPOINT:
            exporter = OTLPSpanExporter(endpoint=OTEL_EXPORTER_OTLP_ENDPOINT)
            processor = BatchSpanProcessor(exporter)
        else:
            exporter = ConsoleSpanExporter()
            processor = SimpleSpanProcessor(exporter)

        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)

        # 2. Setup Structured JSON Logging
        formatter = jsonlogger.JsonFormatter(
            '%(asctime)s %(levelname)s %(name)s %(correlation_id)s %(trace_id)s %(span_id)s %(message)s'
        )

        root_logger = logging.getLogger()

        # Add filter to all existing handlers and swap out the formatter for StreamHandlers
        for handler in root_logger.handlers:
            handler.addFilter(CorrelationFilter())
            if isinstance(handler, logging.StreamHandler):
                handler.setFormatter(formatter)

        # If no stream handler exists, add one
        if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
            logHandler = logging.StreamHandler()
            logHandler.setFormatter(formatter)
            logHandler.addFilter(CorrelationFilter())
            root_logger.addHandler(logHandler)
            root_logger.setLevel(logging.INFO)

    return trace.get_tracer(__name__)
