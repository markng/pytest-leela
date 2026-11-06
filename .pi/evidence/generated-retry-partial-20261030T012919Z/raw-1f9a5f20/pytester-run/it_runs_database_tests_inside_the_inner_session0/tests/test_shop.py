import pytest

from shop.pricing import total


from shop.models import Item


@pytest.mark.django_db
def test_item_round_trips_through_the_database():
    Item.objects.create(name='pen')
    assert Item.objects.count() == 1
    assert total(0, 0) == 0
