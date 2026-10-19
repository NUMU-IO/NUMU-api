"""Meta tokens out of the places a request's URL gets recorded.

Meta's Graph API takes `access_token` in the query string (and its OAuth
exchange `client_secret`), so every recorded URL carried it: the worker's
httpx request log, OpenTelemetry's client spans, Sentry's breadcrumbs.
"""

import logging

from opentelemetry.util import http as otel_http

from src.core import logging as numu_logging
from src.core.logging import REDACTION_MARKER, redact_url_secrets

TOKEN = "EAABsbCS1iHgBAKZBtesttoken"
GRAPH = "https://graph.facebook.com/v21.0/1234567890/events"


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


def test_the_worker_no_longer_logs_request_urls():
    from src.infrastructure.messaging import celery_app

    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.INFO)

    celery_app._quiet_celery_loggers()

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_sentry_gets_no_token_in_breadcrumbs_spans_or_requests():
    from src import main

    crumb = main._redact_breadcrumb(
        {"type": "http", "data": {"url": GRAPH, "http.query": f"access_token={TOKEN}"}},
        {},
    )
    transaction = main._redact_transaction(
        {"spans": [{"data": {"http.query": f"client_secret={TOKEN}&code=AQB1"}}]}, {}
    )
    error = main._filter_sentry_events(
        {
            "request": {
                "url": "https://api.numueg.app/x",
                "query_string": f"access_token={TOKEN}",
            }
        },
        {},
    )

    assert TOKEN not in repr((crumb, transaction, error))
    assert crumb["data"]["url"] == GRAPH
