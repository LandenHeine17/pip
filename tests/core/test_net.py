"""The web libraries' request lines stay out of the journal (rule 1): their addresses hold what someone
asked (?q=), email addresses (calendar ids) and tokens; the online check's deliberate 401 (D-95) with them."""
import logging

from pip_assistant.common.program import setup_logging


def test_web_request_addresses_arent_logged():
    setup_logging("%(message)s")
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpcore").isEnabledFor(logging.INFO)
    assert logging.getLogger("httpx").isEnabledFor(logging.WARNING)
