from pathlib import Path
import os

with Path('imports.log').open('a') as log:
    log.write(str(os.getpid()) + ':' + __name__ + '\n')

from django.contrib import admin

from shop.models import Item


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('name',)
