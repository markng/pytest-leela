import pytest

from shop.pricing import total


import sys

from django.apps import apps
from django.contrib import admin

import shop.admin
from shop.backoffice import ItemAdmin
from shop.mixins import PricedMixin
from shop.models import Item
from stock.models import Bin


def test_registry_and_identity_hold():
    assert admin.site.site_header == 'Shop admin'
    assert apps.get_model('stock', 'Bin') is Bin
    assert issubclass(Item, PricedMixin)
    assert type(admin.site._registry[Item]) is ItemAdmin
    assert sys.modules['shop.mixins'].PricedMixin is PricedMixin
    assert total(2, 3) == 5
