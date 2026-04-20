"""Minimal conftest for V3 theme engine unit tests.

Does NOT import the full application or require env vars.
Tests only the V3 modules in isolation.
"""

import sys
import os

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
