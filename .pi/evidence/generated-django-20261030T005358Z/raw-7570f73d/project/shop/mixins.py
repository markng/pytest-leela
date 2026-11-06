from pathlib import Path
import os

with Path('imports.log').open('a') as log:
    log.write(str(os.getpid()) + ':' + __name__ + '\n')

class PricedMixin:
    currency = 'USD'
