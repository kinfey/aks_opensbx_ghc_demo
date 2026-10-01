import logging
import os

from azure.monitor.opentelemetry import configure_azure_monitor


def configure_telemetry(log_level: str) -> logging.Logger:
    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if connection_string:
        configure_azure_monitor(
            connection_string=connection_string,
            logger_name="mcdonalds_copilot",
        )

    logger = logging.getLogger("mcdonalds_copilot")
    logger.setLevel(log_level.upper())
    return logger
