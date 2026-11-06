import pytest

try:
    import calc  # noqa: F401
except Exception:
    pytest.skip('calc unavailable', allow_module_level=True)