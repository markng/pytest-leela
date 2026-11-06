import pytest

from shop.pricing import total


def test_unblock_reaches_the_real_connection(django_db_blocker):
    from django.db import connection

    with django_db_blocker.unblock():
        name = connection.ensure_connection.__qualname__
    assert name != 'DjangoDbBlocker._blocking_wrapper'
    assert total(0, 0) == 0
