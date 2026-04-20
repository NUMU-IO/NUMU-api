"""Conftest that loads V3 service modules via importlib to bypass Settings validation.

The src.application.__init__.py barrel triggers the full app import chain which
requires JWT keys and DB config. We bypass this by loading the V3 modules directly.
"""

import importlib
import importlib.util
import os
import sys

import pytest

# Ensure NUMU-api root is on path
NUMU_ROOT = os.path.join(os.path.dirname(__file__), "..", "NUMU-api")
sys.path.insert(0, NUMU_ROOT)

# These modules are safe to import normally (no Settings dependency)
from src.core.entities.theme_settings_v3 import (
    BlockInstance,
    ExternalThemeMetadata,
    PageTemplate,
    SectionGroup,
    SectionInstance,
    ThemeSettingsV3,
)


def _load_module(name: str, filepath: str):
    """Load a Python module from a file path without triggering barrel imports."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load V3 service modules directly
_services_dir = os.path.join(NUMU_ROOT, "src", "application", "services")

theme_v3_presets_mod = _load_module(
    "theme_v3_presets", os.path.join(_services_dir, "theme_v3_presets.py")
)
theme_v3_mappers_mod = _load_module(
    "theme_v3_mappers", os.path.join(_services_dir, "theme_v3_mappers.py")
)


@pytest.fixture
def generate_initial_v3_customization():
    return theme_v3_presets_mod.generate_initial_v3_customization


@pytest.fixture
def map_v3_to_legacy():
    return theme_v3_mappers_mod.map_v3_to_legacy_store_settings


@pytest.fixture
def normalize_legacy_to_v3():
    return theme_v3_mappers_mod.normalize_legacy_to_v3


@pytest.fixture
def resolve_theme_settings():
    return theme_v3_mappers_mod.resolve_theme_settings
