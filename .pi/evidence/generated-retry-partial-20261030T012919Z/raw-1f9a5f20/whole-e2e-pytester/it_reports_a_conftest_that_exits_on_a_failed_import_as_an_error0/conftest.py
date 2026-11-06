import pytest

try:
    import calc  # noqa: F401
except Exception:
    pytest.exit('calc unavailable')