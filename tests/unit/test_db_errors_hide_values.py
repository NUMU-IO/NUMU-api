from sqlalchemy.exc import IntegrityError

from src.infrastructure.database.connection import engine


def test_a_database_error_does_not_carry_the_statements_values():
    """``event_handler_failed`` logs ``str(exc)`` and Sentry reports it: with
    the values in it, one failed insert put a shopper's phone in both."""
    error = IntegrityError(
        "INSERT INTO customers (phone) VALUES ($1)",
        ("+201012345678",),
        Exception("duplicate key"),
        hide_parameters=engine.sync_engine.hide_parameters,
    )

    assert "+201012345678" not in str(error)
