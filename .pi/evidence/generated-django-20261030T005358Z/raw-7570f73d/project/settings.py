SECRET_KEY = "leela-e2e"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.admin",
    "shop.apps.ShopConfig",
    "stock",
]
DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
USE_TZ = True