from pathlib import Path
import os

with Path('imports.log').open('a') as log:
    log.write(f'{os.getpid()}:{__name__}\n')

from django.db import models

from shop.mixins import PricedMixin


class Item(PricedMixin, models.Model):
    name = models.CharField(max_length=20)
