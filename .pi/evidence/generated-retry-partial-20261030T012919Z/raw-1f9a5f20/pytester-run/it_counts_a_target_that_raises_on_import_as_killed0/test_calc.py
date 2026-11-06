from calc import ratio


def test_ratio():
    assert ratio() == 5
    assert isinstance(ratio(), int)