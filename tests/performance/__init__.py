"""Performance test suite for NUMU API.

This package contains performance tests for:
- Endpoint response time benchmarks
- Database query performance
- Redis cache performance
- Stress testing scenarios
- 3G network simulation (separate module)

Usage:
    # Run all performance tests
    pytest tests/performance/ -v

    # Run only benchmark tests
    pytest tests/performance/test_endpoint_benchmarks.py -v --benchmark-only

    # Run with benchmark comparison
    pytest tests/performance/test_endpoint_benchmarks.py -v --benchmark-autosave

    # Run stress tests
    pytest tests/performance/test_stress.py -v -s
"""
