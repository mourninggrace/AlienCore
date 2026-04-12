"""
AlienCore - logger.py
Sets up an append-only log file at logs/aliencore.log.
No size limit — the log grows for the life of the installation.
All modules use: logging.getLogger("aliencore.<module>")
"""

import logging
import os
from core.constants import LOG_PATH


def setup(log_enabled: bool = True):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    root = logging.getLogger("aliencore")
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if log_enabled:
        fh = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)

    # Always keep a console handler for debugging / service output
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)
    root.addHandler(ch)

    root.info("AlienCore logger initialized (log_enabled=%s)", log_enabled)
