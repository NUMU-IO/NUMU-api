"""Route-aware head sampler for OpenTelemetry traces.

Selected with ``OTEL_TRACES_SAMPLER=numu`` (entry point in pyproject.toml).
Money paths (checkout, payments, deposits, orders, webhooks) are always
sampled and tagged ``numu.critical`` so the tail sampler in Alloy keeps them;
every other root span is sampled at ``OTEL_TRACES_SAMPLER_ARG`` (default 1.0).
Child spans follow their parent's decision, except that a remote parent that
chose not to sample (Sentry keeps only 10% of hub sessions) does not override
the api's own rule.

Lives at the top of ``src`` on purpose: the SDK loads it before instrumentation
runs, so it must not import any package that builds engines or clients.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from opentelemetry.context import Context
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import Link, SpanKind, TraceState
from opentelemetry.util.types import Attributes

_CRITICAL_PATH = re.compile(
    r"checkout|/pay|payment|instapay|kashier|deposit|/webhooks/|/orders"
)
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class RouteSampler(Sampler):
    def __init__(self, ratio: float) -> None:
        self._default = TraceIdRatioBased(ratio)

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        attrs = dict(attributes or {})
        method = attrs.get("http.request.method") or attrs.get("http.method")
        path = attrs.get("url.path") or attrs.get("http.target") or ""
        if method in _WRITE_METHODS and _CRITICAL_PATH.search(str(path)):
            attrs["numu.critical"] = True
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attrs, trace_state)
        return self._default.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return f"NumuRouteSampler{{{self._default.get_description()}}}"


def numu_sampler(arg: str | None) -> Sampler:
    try:
        ratio = float(arg) if arg else 1.0
    except ValueError:
        ratio = 1.0
    route = RouteSampler(ratio)
    return ParentBased(root=route, remote_parent_not_sampled=route)
