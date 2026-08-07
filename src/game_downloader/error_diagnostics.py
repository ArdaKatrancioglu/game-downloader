from __future__ import annotations

import logging
import traceback

from game_downloader.security import redact_diagnostic


def log_exception(
    logger: logging.Logger,
    operation: str,
    exc: BaseException,
) -> None:
    """Log an exception with its complete cause/context chain."""
    chain = [redact_diagnostic(item) for item in _exception_chain(exc)]
    formatted = redact_diagnostic(
        "".join(traceback.format_exception(exc, chain=True)).rstrip()
    )
    logger.error(
        "Exception diagnostic operation=%s error_type=%s error=%s exception_chain=%s\n%s",
        operation,
        type(exc).__name__,
        redact_diagnostic(str(exc)),
        " -> ".join(chain),
        formatted,
    )


def _exception_chain(exc: BaseException) -> list[str]:
    entries: list[str] = []
    current: BaseException | None = exc
    relation = "exception"
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        entries.append(f"{relation}:{type(current).__name__}({current})")
        if current.__cause__ is not None:
            current = current.__cause__
            relation = "cause"
        elif current.__context__ is not None and not current.__suppress_context__:
            current = current.__context__
            relation = "context"
        else:
            current = None
    return entries
