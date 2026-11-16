"""The online check's deliberate 401 from Groq (no key sent, D-95) stays out of the log; other requests don't."""
import logging

from pip_assistant.core.net import URL


def test_the_online_checks_401_isnt_logged(caplog):
    log = logging.getLogger("httpx")
    with caplog.at_level(logging.INFO, logger="httpx"):
        log.info('HTTP Request: %s %s "%s %d %s"', "GET", URL, "HTTP/1.1", 401, "Unauthorized")
        log.info('HTTP Request: %s %s "%s %d %s"', "POST", "https://api.groq.com/openai/v1/audio/transcriptions",
                 "HTTP/1.1", 200, "OK")
    assert [r.getMessage().split()[3] for r in caplog.records] == ["https://api.groq.com/openai/v1/audio/transcriptions"]
