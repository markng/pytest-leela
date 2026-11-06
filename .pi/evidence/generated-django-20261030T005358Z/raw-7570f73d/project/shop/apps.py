import importlib

from django.apps import AppConfig


class ShopConfig(AppConfig):
    name = 'shop'

    def ready(self):
        importlib.import_module('shop.backoffice')
