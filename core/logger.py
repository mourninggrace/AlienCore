"""
AlienCore - logger.py
Sets up a size-capped, rotating log file at logs/aliencore.log.
Rotation: 10 MB per file, 5 backups (50 MB ceiling) — the log no longer
grows unbounded for the life of the installation.
All modules use: logging.getLogger("aliencore.<module>")
"""

import logging
import logging.handlers
import os
import re
from core.constants import LOG_PATH

# Matches an email address and captures: first char of local-part, the rest
# of the local-part, and the @domain.  Used to mask PII in INFO+ logs.
_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+\-])([A-Za-z0-9._%+\-]*)(@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
)


def _mask_email_match(m: "re.Match") -> str:
    # a***@gmail.com — keep first char of local-part + the domain, mask middle.
    return f"{m.group(1)}***{m.group(3)}"


class EmailMaskingFilter(logging.Filter):
    """Redact email addresses in log records at INFO and above so the on-disk
    log isn't a plaintext dump of user PII.  DEBUG records are left untouched
    so full addresses are still available when explicitly debugging."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno > logging.DEBUG:
            try:
                msg = record.getMessage()
            except Exception:
                return True
            if "@" in msg:
                record.msg = _EMAIL_RE.sub(_mask_email_match, msg)
                record.args = ()
        return True


def setup(log_enabled: bool = True):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    root = logging.getLogger("aliencore")
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    mask = EmailMaskingFilter()

    if log_enabled:
        # Rotating handler: 10 MB/file × 5 backups = 50 MB hard ceiling, so the
        # log can never silently fill the disk.  Old plain FileHandler grew
        # forever and accumulated unbounded PII.
        fh = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        fh.addFilter(mask)
        root.addHandler(fh)

    # Always keep a console handler for debugging / service output
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)
    ch.addFilter(mask)
    root.addHandler(ch)

    root.info("AlienCore logger initialized (log_enabled=%s)", log_enabled)
