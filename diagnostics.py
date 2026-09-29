import logging
from collections import Counter, deque
from datetime import datetime, timezone

import config

START_TIME = datetime.now(timezone.utc)


def uptime_seconds() -> float:
    return (datetime.now(timezone.utc) - START_TIME).total_seconds()


# --- Recent log capture -------------------------------------------------------
# A ring buffer of recent WARNING+ records from anywhere in the process, so a
# debug command can show "what went wrong recently" without needing log file access.

class _RecentLogHandler(logging.Handler):
    def __init__(self, capacity: int = 50):
        super().__init__(level=logging.WARNING)
        self.records: deque[str] = deque(maxlen=capacity)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:
            pass  # A broken handler must never be what crashes the process.


_recent_log_handler: _RecentLogHandler | None = None


def attach_recent_log_handler() -> None:
    """Install the ring-buffer handler on the root logger. Call once at startup."""
    global _recent_log_handler
    if _recent_log_handler is not None:
        return  # Already attached; avoid double-logging on a reconnect or re-import.
    _recent_log_handler = _RecentLogHandler()
    logging.getLogger().addHandler(_recent_log_handler)


def get_recent_logs(limit: int = 10) -> list[str]:
    if _recent_log_handler is None:
        return []
    return list(_recent_log_handler.records)[-limit:]


# --- Command usage tracking ---------------------------------------------------

_invocations: Counter[str] = Counter()
_errors: Counter[str] = Counter()


def record_invocation(command_name: str) -> None:
    _invocations[command_name] += 1


def record_error(command_name: str) -> None:
    _errors[command_name] += 1


def get_command_stats(top: int = 10) -> tuple[list[tuple[str, int]], int, int]:
    """Returns (top N commands by use, total invocations, total errors)."""
    return _invocations.most_common(top), sum(_invocations.values()), sum(_errors.values())


# --- Configuration validation --------------------------------------------------

def validate_config() -> list[str]:
    """Warnings about the .env configuration, each tagged with its /syscheck code.
    An empty list means all clear."""
    from error_codes import CODES
    from syscheck import Report, check_config

    report = Report()
    check_config(report, config)
    return [
        f"{finding.code} {CODES[finding.code].title}: {finding.detail} Fix: {CODES[finding.code].fix}"
        for finding in report.findings
    ]
