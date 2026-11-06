import pytest

import calc

if calc.LIMIT != 2:
    pytest.skip('limit changed', allow_module_level=True)


def test_double():
    assert calc.double(3) == 6