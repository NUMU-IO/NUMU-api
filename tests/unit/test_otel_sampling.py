from opentelemetry.sdk.trace.sampling import Decision

from src.otel_sampling import RouteSampler, numu_sampler

_NEVER = RouteSampler(0.0)


def _sample(method: str, path: str):
    return _NEVER.should_sample(
        None, 0x5F00, "span", attributes={"http.method": method, "http.target": path}
    )


def test_money_writes_are_always_sampled_and_tagged() -> None:
    for path in (
        "/api/v1/storefront/store/abc/checkout",
        "/api/v1/stores/abc/orders/1/payment-proof",
        "/api/v1/webhooks/kashier",
    ):
        result = _sample("POST", path)
        assert result.decision is Decision.RECORD_AND_SAMPLE, path
        assert result.attributes["numu.critical"] is True


def test_reads_and_other_routes_use_the_ratio() -> None:
    assert (
        _sample("GET", "/api/v1/storefront/store/abc/checkout").decision
        is Decision.DROP
    )
    assert _sample("POST", "/api/v1/products").decision is Decision.DROP


def test_factory_defaults_to_full_sampling_on_bad_arg() -> None:
    assert "1.0" in numu_sampler("not-a-number").get_description()
