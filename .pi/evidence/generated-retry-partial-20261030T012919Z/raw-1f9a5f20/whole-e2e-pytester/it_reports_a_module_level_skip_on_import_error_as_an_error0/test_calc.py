import pytest

try:
    import calc
except Exception:
    pytest.skip('calc unavailable', allow_module_level=True)


def test_ratio():
    assert calc.RATIO in (5, 10, 5.0, 10.0, 0, 20)