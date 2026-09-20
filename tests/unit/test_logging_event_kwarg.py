from unittest.mock import MagicMock

from src.core.logging import Log


def _log_with_spy() -> tuple[Log, MagicMock]:
    log = Log("test")
    spy = MagicMock()
    object.__setattr__(log, "_log", spy)
    return log, spy


def test_context_named_event_does_not_break_the_call():
    """Six call sites passed `event=` and raised TypeError mid-request."""
    log, spy = _log_with_spy()

    log.info("webhook_dispatch_skipped", event="order.paid", store_id="s1")

    spy.info.assert_called_once_with(
        "webhook_dispatch_skipped", event_name="order.paid", store_id="s1"
    )


def test_every_level_keeps_the_context():
    log, spy = _log_with_spy()

    log.warning("w", event="order.created")
    log.error("e", event="order.created")
    log.debug("d", event="order.created")
    log.insight("i", event="order.created")

    assert spy.warning.call_args.kwargs == {"event_name": "order.created"}
    assert spy.error.call_args.kwargs == {"event_name": "order.created"}
    assert spy.debug.call_args.kwargs == {"event_name": "order.created"}
    assert spy.info.call_args.kwargs == {
        "kind": "insight",
        "event_name": "order.created",
    }


def test_binding_context_named_event_is_kept_too():
    log, spy = _log_with_spy()

    log.bind(event="order.paid")

    spy.bind.assert_called_once_with(event_name="order.paid")
