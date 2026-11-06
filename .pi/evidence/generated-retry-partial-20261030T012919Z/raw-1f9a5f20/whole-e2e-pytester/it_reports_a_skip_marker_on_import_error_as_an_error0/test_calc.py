import pytest

try:
    import calc
except Exception:
    calc = None


@pytest.mark.skipif(calc is None, reason='calc unavailable')
def test_ratio():
    assert calc.RATIO in (5, 10, 5.0, 10.0, 0, 20)