import builtins

from calc import double


def test_double_once():
    assert not getattr(builtins, '_leela_e2e_seen', False)
    builtins._leela_e2e_seen = True
    assert double(3) == 6