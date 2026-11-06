from pathlib import Path
import os

with Path('imports.log').open('a') as log:
    log.write(f'{os.getpid()}:{__name__}\n')

from django.contrib import admin

admin.site.site_header = 'Shop admin'
