"""
AlienCore - logger.py
Sets up a rotating log file at logs/aliencore.log.
All modules use: logging.getLogger("aliencore.<module>")
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from core.constants import LOG_PATH


def setup(log_enabled: bool = True, max_mb: int = 10):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    root = logging.getLogger("aliencore")
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if log_enabled:
        fh = RotatingFileHandler(
            LOG_PATH,
            maxBytes=max_mb * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)

    # Always keep a console handler for debugging / service output
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)
    root.addHandler(ch)

    root.info("AlienCore logger initialized (log_enabled=%s, max_mb=%s)", log_enabled, max_mb)
