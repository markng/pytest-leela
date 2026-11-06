from pathlib import Path
import os

with Path('imports.log').open('a') as log:
    log.write(str(os.getpid()) + ':' + __name__ + '\n')

from django.db import models


class Bin(models.Model):
    label = models.CharField(max_length=20)
