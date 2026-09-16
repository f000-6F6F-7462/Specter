import logging
from collections.abc import Iterator

import pytest

from specter.core.logging import LogFormat, configure_logging


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root_logger = logging.getLogger()
    httpx_logger = logging.getLogger("httpx")
    original_handlers = root_logger.handlers[:]
    original_root_level = root_logger.level
    original_httpx_level = httpx_logger.level
    yield
    root_logger.handlers[:] = original_handlers
    root_logger.setLevel(original_root_level)
    httpx_logger.setLevel(original_httpx_level)


def test_request_urls_are_not_logged_when_logging_is_configured_for_debugging() -> None:
    configure_logging("DEBUG", LogFormat.TEXT)

    httpx_logger = logging.getLogger("httpx")

    assert not httpx_logger.isEnabledFor(logging.INFO)
    assert httpx_logger.isEnabledFor(logging.WARNING)
