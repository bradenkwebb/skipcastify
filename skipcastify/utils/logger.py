import logging
import os
from logging.handlers import RotatingFileHandler

COLORS = {
    'DEBUG': '\033[36m',    # Cyan
    'INFO': '\033[32m',     # Green
    'WARNING': '\033[33m',  # Yellow
    'ERROR': '\033[31m',    # Red
    'CRITICAL': '\033[41m', # Red background
    'RESET': '\033[0m'      # Reset
}

class ColorFormatter(logging.Formatter):
    """Custom formatter that adds colors to console output"""
    def format(self, record):
        # Save original levelname
        original_levelname = record.levelname
        # Add color to levelname
        record.levelname = (f"{COLORS.get(record.levelname, '')}"
                          f"{record.levelname}{COLORS['RESET']}")
        # Get formatted string
        result = super().format(record)
        # Restore original levelname
        record.levelname = original_levelname
        return result

def setup_logging(data_dir: str):
    """Configure logging with both file and console handlers"""
    log_dir = os.path.join(data_dir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    # Create formatters
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_formatter = ColorFormatter(
        '%(levelname)s: %(message)s'
    )
    
    # Create handlers
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'skipcastify.log'),
        maxBytes=1024*1024,  # 1MB
        backupCount=5
    )
    file_handler.setFormatter(file_formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)