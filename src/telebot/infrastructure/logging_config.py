import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logging(log_dir: str = "logs", log_level: int = logging.DEBUG):
    """
    Sets up centralized logging for the telebot application.
    Logs to both console (INFO) and a persistent file (DEBUG).
    """
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "telebot.log")

    # Clear existing handlers
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    root_logger.setLevel(log_level)

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )

    # Console Handler (INFO)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File Handler (DEBUG, Rotating)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Suppress noisy external loggers
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("groq").setLevel(logging.WARNING)

    logging.info(f"Logging initialized. Log file: {log_file}")
