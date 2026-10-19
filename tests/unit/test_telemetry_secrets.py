"""Meta tokens out of the places a request's URL gets recorded.

Meta's Graph API takes `access_token` in the query string (and its OAuth
exchange `client_secret`), so every recorded URL carried it: the worker's
httpx request log, OpenTelemetry's client spans, Sentry's breadcrumbs, and
the text of an `HTTPStatusError`, which embeds the full request URL.
"""

import io
import logging

import httpx
from opentelemetry.util import http as otel_http

from src.core import logging as numu_logging
from src.core.logging import REDACTION_MARKER, redact_url_secrets

TOKEN = "EAABsbCS1iHgBAKZBtesttoken"
GRAPH = "https://graph.facebook.com/v21.0/1234567890/events"
EXCHANGE = (
    "https://graph.facebook.com/v21.0/oauth/access_token"
    f"?client_id=42&client_secret={TOKEN}&code=AQB1&redirect_uri=x"
)


def _meta_error() -> httpx.HTTPStatusError:
    """What `raise_for_status()` raises when Meta refuses an OAuth exchange."""
    try:
        httpx.Response(400, request=httpx.Request("GET", EXCHANGE)).raise_for_status()
    except httpx.HTTPStatusError as exc:
        assert TOKEN in str(exc)  # the message embeds the full URL
        return exc
    raise AssertionError("no error raised")


def test_secret_values_are_replaced_and_everything_else_kept():
    assert (
        redact_url_secrets(f"{GRAPH}?access_token={TOKEN}&fields=id")
        == f"{GRAPH}?access_token={REDACTION_MARKER}&fields=id"
    )
    exchange = f"client_id=42&client_secret={TOKEN}&code=AQB1&redirect_uri=x"
    assert redact_url_secrets(exchange) == (
        f"client_id=42&client_secret={REDACTION_MARKER}"
        f"&code={REDACTION_MARKER}&redirect_uri=x"
    )
    assert redact_url_secrets("fields=id,name&limit=5&codex=1") == (
        "fields=id,name&limit=5&codex=1"
    )


def test_traced_urls_lose_the_token(monkeypatch):
    # OpenTelemetry's own list (the app may already have extended it on import).
    defaults = ["AWSAccessKeyId", "Signature", "sig", "X-Goog-Signature"]
    monkeypatch.setattr(otel_http, "PARAMS_TO_REDACT", defaults)
    assert TOKEN in otel_http.redact_url(f"{GRAPH}?access_token={TOKEN}")

    numu_logging._redact_secrets_in_traces()

    assert TOKEN not in otel_http.redact_url(f"{GRAPH}?access_token={TOKEN}&fields=id")


def test_the_worker_logs_neither_request_urls_nor_tokens_in_errors():
    from src.infrastructure.messaging import celery_app

    worker_root = logging.getLogger("test.worker.root")
    worker_root.propagate = False
    stream = io.StringIO()
    worker_root.addHandler(logging.StreamHandler(stream))
    logging.getLogger("httpx").setLevel(logging.INFO)

    celery_app._quiet_celery_loggers(logger=worker_root)
    try:
        raise _meta_error()
    except httpx.HTTPStatusError:
        worker_root.exception(
            "capi_send_failed url=%s", f"{GRAPH}?access_token={TOKEN}"
        )

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    written = stream.getvalue()
    assert "capi_send_failed" in written and "HTTPStatusError" in written
    assert TOKEN not in written


def test_an_api_log_line_with_a_meta_error_shows_no_token(capsys):
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        numu_logging.configure_logging()
        log = numu_logging.get_logger("test.meta")
        try:
            raise _meta_error()
        except httpx.HTTPStatusError as exc:
            log.exception("meta_exchange_failed", error=str(exc))
        written = capsys.readouterr().out
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)

    assert "meta_exchange_failed" in written
    assert TOKEN not in written


def test_sentry_gets_no_token_anywhere_in_an_event():
    from src import main

    message = str(_meta_error())
    error = main._filter_sentry_events(
        {
            "exception": {
                "values": [
                    {
                        "type": "HTTPStatusError",
                        "value": message,
                        "stacktrace": {
                            "frames": [
                                {
                                    "vars": {
                                        "url": f"'{EXCHANGE}'",
                                        "params": {"access_token": f"'{TOKEN}'"},
                                    }
                                }
                            ]
                        },
                    }
                ]
            },
            "request": {
                "url": "https://api.numueg.app/x",
                "query_string": f"access_token={TOKEN}",
            },
        },
        {},
    )
    crumb = main._scrub(
        {"type": "http", "data": {"url": GRAPH, "http.query": f"access_token={TOKEN}"}},
        {},
    )
    transaction = main._scrub(
        {"spans": [{"data": {"http.query": f"client_secret={TOKEN}&code=AQB1"}}]}, {}
    )

    assert TOKEN not in repr((error, crumb, transaction))
    assert crumb["data"]["url"] == GRAPH
    assert "400 Bad Request" in error["exception"]["values"][0]["value"]
