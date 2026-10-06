"""End-to-end: ``pytest --leela`` in a subprocess against throwaway projects."""

import importlib
import textwrap

_DOUBLE = "def double(x):\n    return x * 2\n"
_DOUBLE_TEST = (
    "from calc import double\n\n\ndef test_double():\n    assert double(3) == 6\n"
)


def _leela(pytester, *extra):
    return pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "--leela", "--target", "calc.py", *extra
    )


def describe_import_breaking_mutant():
    def it_counts_a_target_that_raises_on_import_as_killed(pytester):
        """The judge's probe: every test errors at collection, the suite is red."""
        pytester.makepyfile(
            calc="RATIO = 10 // (1 + 1)\n\n\ndef ratio():\n    return RATIO\n",
            test_calc=(
                "from calc import ratio\n\n\ndef test_ratio():\n"
                "    assert ratio() == 5\n"
                "    assert isinstance(ratio(), int)\n"
            ),
        )

        result = _leela(pytester)

        assert result.ret == 0
        result.stdout.fnmatch_lines(["Overall: 5/5 killed (100.0%)*"])
        assert "ERROR" not in result.stdout.str()

    def it_keeps_a_caught_import_error_with_green_tests_survived(pytester):
        """Regression: the test swallows the import failure and passes, so the
        mutant that breaks the import went undetected."""
        pytester.makepyfile(
            calc="DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n",
            test_calc=(
                "try:\n"
                "    import calc\n"
                "except Exception:\n"
                "    calc = None\n\n\n"
                "def test_ratio_if_available():\n"
                "    if calc is None:\n"
                "        return\n"
                "    assert calc.RATIO in (5, 10, 5.0, 10.0, 0, 20)\n"
            ),
        )

        result = _leela(pytester)

        result.stdout.fnmatch_lines(["*line 1: + → - * SURVIVED"])
        assert "ERROR" not in result.stdout.str()
        assert result.ret == 1


def describe_error_mutants():
    def _project(pytester, fail_on_error=None):
        # Every mutant of LIMIT trips the test module's own import-time
        # guard: the error comes from the test file, not from the target.
        pytester.makepyfile(
            calc="LIMIT = 1 + 1\n\n\n" + _DOUBLE,
            test_calc=(
                "import calc\n\n"
                "if calc.LIMIT != 2:\n"
                "    raise RuntimeError('limit changed')\n\n\n"
                "def test_double():\n"
                "    assert calc.double(3) == 6\n"
            ),
        )
        if fail_on_error is not None:
            pytester.makefile(
                ".toml",
                pyproject=f"[tool.pytest-leela]\nfail_on_error = {fail_on_error}\n",
            )

    def it_shows_the_error_and_fails_the_session(pytester):
        _project(pytester)

        result = _leela(pytester)

        assert result.ret == 1
        result.stdout.fnmatch_lines(
            [
                "*line 1: + * ERROR",
                (
                    "*pytest exited with USAGE_ERROR (test_calc.py: E   RuntimeError:"
                    " limit changed)"
                ),
                "*mutants errored outside the tests*",
            ]
        )
        assert "SURVIVED" not in result.stdout.str()

    def it_passes_when_fail_on_error_is_false(pytester):
        _project(pytester, fail_on_error="false")

        result = _leela(pytester)

        assert result.ret == 0
        result.stdout.fnmatch_lines(["*ERROR"])


def describe_baseline_abort():
    def it_aborts_when_a_test_only_passes_once_per_process(pytester):
        pytester.makepyfile(
            calc=_DOUBLE,
            test_calc=(
                "import builtins\n\nfrom calc import double\n\n\n"
                "def test_double_once():\n"
                "    assert not getattr(builtins, '_leela_e2e_seen', False)\n"
                "    builtins._leela_e2e_seen = True\n"
                "    assert double(3) == 6\n"
            ),
        )

        result = _leela(pytester)

        assert result.ret == 1
        result.stdout.fnmatch_lines(
            [
                (
                    "leela: aborted: tests do not pass with no mutation applied "
                    "(failed: test_calc.py::test_double_once)*"
                )
            ]
        )
        assert "Overall:" not in result.stdout.str()


def describe_clean_project():
    def it_kills_every_mutant_and_passes(pytester):
        pytester.makepyfile(calc=_DOUBLE, test_calc=_DOUBLE_TEST)

        result = _leela(pytester)

        assert result.ret == 0
        result.stdout.fnmatch_lines(["Overall: *killed (100.0%)*"])


_LOGGED = (
    "from pathlib import Path\n\n"
    "with Path('imports.log').open('a') as log:\n"
    "    log.write(__name__ + '\\n')\n\n"
)


def _django_project(pytester, test_body):
    """A small Django project on sqlite.

    Every project module appends its name to imports.log when executed, so
    a test can count re-executions across leela's inner sessions.
    ``shop.backoffice`` registers a project-defined ModelAdmin and is
    imported only from ``ShopConfig.ready()``: nothing references it, so
    only leela's walk of the admin registry can keep it loaded.
    """
    # Hard import: a missing pytest-django must fail, never skip.
    importlib.import_module("pytest_django")
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            DJANGO_SETTINGS_MODULE = settings
            pythonpath = .
            """
        )
    )
    pytester.makepyfile(
        settings=textwrap.dedent(
            """
            SECRET_KEY = "leela-e2e"
            INSTALLED_APPS = [
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.messages",
                "django.contrib.sessions",
                "django.contrib.admin",
                "shop.apps.ShopConfig",
            ]
            DATABASES = {
                "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
            }
            DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
            USE_TZ = True
            """
        )
    )
    shop = pytester.mkpydir("shop")
    (shop / "apps.py").write_text(
        "import importlib\n\n"
        "from django.apps import AppConfig\n\n\n"
        "class ShopConfig(AppConfig):\n"
        "    name = 'shop'\n\n"
        "    def ready(self):\n"
        "        importlib.import_module('shop.backoffice')\n"
    )
    (shop / "mixins.py").write_text(
        _LOGGED + "class PricedMixin:\n    currency = 'USD'\n"
    )
    (shop / "models.py").write_text(
        _LOGGED + "from django.db import models\n\n"
        "from shop.mixins import PricedMixin\n\n\n"
        "class Item(PricedMixin, models.Model):\n"
        "    name = models.CharField(max_length=20)\n"
    )
    (shop / "backoffice.py").write_text(
        _LOGGED + "from django.contrib import admin\n\n"
        "from shop.models import Item\n\n\n"
        "@admin.register(Item)\n"
        "class ItemAdmin(admin.ModelAdmin):\n"
        "    list_display = ('name',)\n"
    )
    (shop / "pricing.py").write_text("def total(a, b):\n    return a + b\n")
    tests = pytester.mkdir("tests")
    (tests / "test_shop.py").write_text(
        "import pytest\n\nfrom shop.pricing import total\n\n\n" + test_body
    )
    return pytester


def _leela_django(project):
    return project.runpytest_subprocess(
        "-p", "no:cacheprovider", "--leela", "--target", "shop/pricing.py"
    )


def describe_django_project():
    def it_runs_database_tests_inside_the_inner_session(pytester):
        """total(0, 0) cannot tell + from -, so Add -> Sub must survive.

        0.8.0 reported it killed: the DB test errored in setup inside every
        inner run.
        """
        project = _django_project(
            pytester,
            "from shop.models import Item\n\n\n"
            "@pytest.mark.django_db\n"
            "def test_item_round_trips_through_the_database():\n"
            "    Item.objects.create(name='pen')\n"
            "    assert Item.objects.count() == 1\n"
            "    assert total(0, 0) == 0\n",
        )

        result = _leela_django(project)

        assert "leela: aborted" not in result.stdout.str()
        result.stdout.fnmatch_lines(["*line 2: + * SURVIVED"])
        assert result.ret == 1

    def it_lifts_the_outer_db_block_inside_inner_sessions(pytester):
        """Root cause 3 without touching a database.

        Inside an inner session, unblock() must restore the real
        ensure_connection, not the outer session's blocking wrapper.
        """
        project = _django_project(
            pytester,
            "def test_unblock_reaches_the_real_connection(django_db_blocker):\n"
            "    from django.db import connection\n\n"
            "    with django_db_blocker.unblock():\n"
            "        name = connection.ensure_connection.__qualname__\n"
            "    assert name != 'DjangoDbBlocker._blocking_wrapper'\n"
            "    assert total(0, 0) == 0\n",
        )

        result = _leela_django(project)

        assert "leela: aborted" not in result.stdout.str()
        result.stdout.fnmatch_lines(["*line 2: + * SURVIVED"])

    def it_never_re_executes_models_admin_or_what_they_reference(pytester):
        """shop.models is pinned as a model module, shop.backoffice through
        the admin registry walk, shop.mixins through the reference closure.
        Each must execute exactly once across the outer and inner sessions.
        """
        project = _django_project(
            pytester,
            "import sys\n\n"
            "from django.contrib import admin\n\n"
            "from shop.backoffice import ItemAdmin\n"
            "from shop.mixins import PricedMixin\n"
            "from shop.models import Item\n\n\n"
            "def test_registry_and_identity_hold():\n"
            "    assert issubclass(Item, PricedMixin)\n"
            "    assert type(admin.site._registry[Item]) is ItemAdmin\n"
            "    assert sys.modules['shop.mixins'].PricedMixin is PricedMixin\n"
            "    assert total(2, 3) == 5\n",
        )

        result = _leela_django(project)

        executions = (project.path / "imports.log").read_text().split()
        assert sorted(executions) == ["shop.backoffice", "shop.mixins", "shop.models"]
        result.stdout.fnmatch_lines(["Overall: 3/3 killed (100.0%)*"])
        assert result.ret == 0
