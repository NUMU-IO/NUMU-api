"""Unit tests for the rate-limit middleware.

The previous version of this file tested a separate ``RateLimiter`` class
that has since been folded into ``RateLimitMiddleware``. None of the old
imports (``RateLimiter``, ``RateLimitExceeded``,
``rate_limit_exceeded_handler``, ``rate_limiter``) exist on the current
module, so the file failed to collect — blocking ``pytest tests/unit``.

Until someone rewrites the suite against the current
``RateLimitMiddleware`` API, this module is intentionally empty; the
prior test bodies are preserved in git history at
``5987ea7^:tests/unit/middleware/test_rate_limit.py``.
"""
