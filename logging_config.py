#
# File: logging_config.py
# Revision: 2
# Description: Corrects the INFO format to include the log level,
# ensuring the detailed format is only used for DEBUG mode.
#

import logging

# Define two different formats
DEBUG_FORMAT = '[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s'
INFO_FORMAT = '[%(levelname)s] %(message)s'  # <-- Corrected format

# Store the current state
is_debug_mode = False

def configure_logging(debug_mode: bool = False):
    """
    Configures the root logger with a specific level and format.
    
    Args:
        debug_mode: If True, sets logging to DEBUG level with a detailed format.
                    Otherwise, sets to INFO level with a clean format.
    """
    global is_debug_mode
    is_debug_mode = debug_mode
    
    level = logging.DEBUG if debug_mode else logging.INFO
    log_format = DEBUG_FORMAT if debug_mode else INFO_FORMAT
    
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers to avoid duplication
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(log_format, datefmt='%Y-%m-%d %H:%M:%S'))
    root_logger.addHandler(handler)
    
    # Quieten down noisy libraries, but allow them to show in debug mode
    if not debug_mode:
        logging.getLogger("google.auth.transport.requests").setLevel(logging.WARNING)
        logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)

def toggle_debug_mode():
    """Toggles the logging configuration between INFO and DEBUG mode."""
    global is_debug_mode
    configure_logging(not is_debug_mode)
    return not is_debug_mode