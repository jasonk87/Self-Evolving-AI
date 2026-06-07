import logging
import contextvars
from pythonjsonlogger import jsonlogger
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter

# ContextVar to store the correlation ID across async calls
correlation_id_var = contextvars.ContextVar('correlation_id', default=None)

class CorrelationFilter(logging.Filter):
    """
    Injects correlation_id into the log record.
    """
    def filter(self, record):
        correlation_id = correlation_id_var.get()
        if correlation_id:
            record.correlation_id = correlation_id
        return True

def setup_logging_and_tracing():
    """
    Configures standard structured logging and sets up OpenTelemetry tracer.
    """
    # 1. Setup OpenTelemetry
    provider = TracerProvider()
    processor = SimpleSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    # 2. Setup Structured JSON Logging
    logHandler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(levelname)s %(name)s %(correlation_id)s %(message)s'
    )
    logHandler.setFormatter(formatter)

    # 3. Add Filter
    logHandler.addFilter(CorrelationFilter())

    # Optional: configure the root logger to use this handler
    # Note: we might want to do this carefully if other modules already configure root.
    root_logger = logging.getLogger()
    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    root_logger.addHandler(logHandler)
    root_logger.setLevel(logging.INFO)

    return trace.get_tracer(__name__)
