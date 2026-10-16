from __future__ import annotations

import logging
import re

REDACTED = "***REDACTED***"

_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(password\s*[=:]\s*)(\S+)"),
    re.compile(r"(?i)(authorization\s*[=:]\s*)(\S+(?:\s+\S+)?)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]+)"),
    re.compile(r"(?i)(basic\s+)([A-Za-z0-9+/=]+)"),
    re.compile(r"(?i)(api[_-]?token\s*[=:]\s*)(\S+)"),
    re.compile(r"(?i)(token\s*[=:]\s*)(\S+)"),
)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def redact(value: str) -> str:
    """Reemplaza credenciales, tokens y encabezados de autorización reconocibles."""
    for pattern in _REDACTION_PATTERNS:
        value = pattern.sub(lambda match: match.group(1) + REDACTED, value)
    return value


class RedactingFilter(logging.Filter):
    """Filtro que enmascara secretos antes de emitir cada registro de log."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            original = record.getMessage()
        except Exception:
            return True
        redacted = redact(original)
        if redacted != original:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Configura logging a nivel raíz con un filtro de enmascaramiento de secretos.

    Los loggers de ``httpx``/``httpcore`` se fijan en WARNING para evitar que se
    registren encabezados de petición (incluida la autenticación) en nivel DEBUG.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore2").setLevel(logging.WARNING)
