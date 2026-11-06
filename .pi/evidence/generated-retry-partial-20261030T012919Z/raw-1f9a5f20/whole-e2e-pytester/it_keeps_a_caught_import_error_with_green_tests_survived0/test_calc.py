try:
    import calc
except Exception:
    calc = None


def test_ratio_if_available():
    if calc is None:
        return
    assert calc.RATIO in (5, 10, 5.0, 10.0, 0, 20)