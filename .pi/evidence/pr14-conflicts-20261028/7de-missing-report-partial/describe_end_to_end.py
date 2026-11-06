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

    def it_reports_a_module_level_skip_on_import_error_as_an_error(pytester):
        """Regression: the test module skips itself when the import fails, so
        pytest reports "1 skipped" and no test saw the mutant."""
        pytester.makepyfile(
            calc="DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n",
            test_calc=(
                "import pytest\n\n"
                "try:\n"
                "    import calc\n"
                "except Exception:\n"
                "    pytest.skip('calc unavailable', allow_module_level=True)\n\n\n"
                "def test_ratio():\n"
                "    assert calc.RATIO in (5, 10, 5.0, 10.0, 0, 20)\n"
            ),
        )

        result = _leela(pytester)

        result.stdout.fnmatch_lines(
            [
                "*line 1: + → - * ERROR",
                "*pytest exited with USAGE_ERROR",
            ]
        )
        assert "KILLED" not in result.stdout.str()
        assert result.ret == 1

    def it_reports_a_skip_marker_on_import_error_as_an_error(pytester):
        """Regression: every test skips at setup when the import fails, pytest
        exits OK with zero tests run."""
        pytester.makepyfile(
            calc="DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n",
            test_calc=(
                "import pytest\n\n"
                "try:\n"
                "    import calc\n"
                "except Exception:\n"
                "    calc = None\n\n\n"
                "@pytest.mark.skipif(calc is None, reason='calc unavailable')\n"
                "def test_ratio():\n"
                "    assert calc.RATIO in (5, 10, 5.0, 10.0, 0, 20)\n"
            ),
        )

        result = _leela(pytester)

        result.stdout.fnmatch_lines(["*line 1: + → - * ERROR", "*no tests ran"])
        assert "KILLED" not in result.stdout.str()
        assert result.ret == 1


def describe_collection_failures():
    def it_kills_when_a_module_level_assert_fails_under_the_mutant(pytester):
        """The judge's modassert probe: the test module fails to collect under
        the mutant, after a green baseline, so the suite detected it."""
        pytester.makepyfile(
            calc=(
                "DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n\n\n"
                "def ratio():\n    return RATIO\n"
            ),
            test_calc=(
                "import calc\n\n"
                "assert calc.RATIO == 5\n\n\n"
                "def test_ratio():\n"
                "    assert calc.ratio() == 5\n"
            ),
        )

        result = _leela(pytester)

        result.stdout.fnmatch_lines(
            ["*line 2: // → / * SURVIVED", "Overall: 4/5 killed (80.0%)*"]
        )
        assert "ERROR" not in result.stdout.str()
        assert result.ret == 1

    def it_reports_a_conftest_that_skips_itself_as_an_error(pytester):
        """The judge's confskip probe: the conftest's module-level skip used
        to escape pytest.main and crash the whole run."""
        pytester.makepyfile(
            calc="DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n",
            conftest=(
                "import pytest\n\n"
                "try:\n"
                "    import calc  # noqa: F401\n"
                "except Exception:\n"
                "    pytest.skip('calc unavailable', allow_module_level=True)\n"
            ),
            test_calc=(
                "import calc\n\n\n"
                "def test_ratio():\n"
                "    assert calc.RATIO in (5, 10, 0, 20, 5.0)\n"
            ),
        )

        result = _leela(pytester)

        result.stdout.fnmatch_lines(
            [
                "*line 1: + → - * ERROR",
                "*pytest crashed: Skipped: calc unavailable",
            ]
        )
        assert "Traceback" not in result.stdout.str() + result.stderr.str()
        assert result.ret == 1

    def it_reports_a_conftest_that_exits_on_a_failed_import_as_an_error(pytester):
        """The judge's confexit probe, while a conftest the target itself
        breaks stays a kill."""
        pytester.makepyfile(
            calc="DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n",
            conftest=(
                "import pytest\n\n"
                "try:\n"
                "    import calc  # noqa: F401\n"
                "except Exception:\n"
                "    pytest.exit('calc unavailable')\n"
            ),
            test_calc=(
                "import calc\n\n\n"
                "def test_ratio():\n"
                "    assert calc.RATIO in (5, 10, 0, 20, 5.0)\n"
            ),
        )

        result = _leela(pytester)

        result.stdout.fnmatch_lines(
            [
                "*line 1: + → - * ERROR",
                "*conftest called pytest.exit() at import (calc unavailable)",
            ]
        )
        assert "KILLED" not in result.stdout.str()
        assert result.ret == 1

    def it_reports_a_test_module_that_exits_on_a_failed_import_as_an_error(
        pytester,
    ):
        """The judge's modexit probe."""
        pytester.makepyfile(
            calc="DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n",
            test_calc=(
                "import pytest\n\n"
                "try:\n"
                "    import calc\n"
                "except Exception:\n"
                "    pytest.exit('calc unavailable')\n\n\n"
                "def test_ratio():\n"
                "    assert calc.RATIO in (5, 10, 5.0, 10.0, 0, 20)\n"
            ),
        )

        result = _leela(pytester)

        result.stdout.fnmatch_lines(
            [
                "*line 1: + → - * ERROR",
                "*test module test_calc.py called pytest.exit() at import (calc unavailable)",
            ]
        )
        assert "KILLED" not in result.stdout.str()
        assert result.ret == 1


def describe_error_mutants():
    def _project(pytester, fail_on_error=None):
        # Every mutant of LIMIT makes the test module skip itself, so no
        # test runs against it: an error, neither a kill nor a survival.
        pytester.makepyfile(
            calc="LIMIT = 1 + 1\n\n\n" + _DOUBLE,
            test_calc=(
                "import pytest\n\n"
                "import calc\n\n"
                "if calc.LIMIT != 2:\n"
                "    pytest.skip('limit changed', allow_module_level=True)\n\n\n"
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
                "*pytest exited with USAGE_ERROR",
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
    ``shop.admin`` registers nothing and ``stock`` has no admin, so only the
    ``<app>.admin`` and models-module roots keep those modules loaded.
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
                "stock",
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
    (shop / "admin.py").write_text(
        _LOGGED + "from django.contrib import admin\n\n"
        "admin.site.site_header = 'Shop admin'\n"
    )
    stock = pytester.mkpydir("stock")
    (stock / "models.py").write_text(
        _LOGGED + "from django.db import models\n\n\n"
        "class Bin(models.Model):\n"
        "    label = models.CharField(max_length=20)\n"
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
        """Each pinning mechanism keeps one module loaded: shop.models and
        stock.models as model modules, shop.admin as an ``<app>.admin``,
        shop.backoffice through the admin registry walk, shop.mixins through
        the reference closure.  Each must execute exactly once across the
        outer and inner sessions.
        """
        project = _django_project(
            pytester,
            "import sys\n\n"
            "from django.apps import apps\n"
            "from django.contrib import admin\n\n"
            "import shop.admin\n"
            "from shop.backoffice import ItemAdmin\n"
            "from shop.mixins import PricedMixin\n"
            "from shop.models import Item\n"
            "from stock.models import Bin\n\n\n"
            "def test_registry_and_identity_hold():\n"
            "    assert admin.site.site_header == 'Shop admin'\n"
            "    assert apps.get_model('stock', 'Bin') is Bin\n"
            "    assert issubclass(Item, PricedMixin)\n"
            "    assert type(admin.site._registry[Item]) is ItemAdmin\n"
            "    assert sys.modules['shop.mixins'].PricedMixin is PricedMixin\n"
            "    assert total(2, 3) == 5\n",
        )

        result = _leela_django(project)

        executions = (project.path / "imports.log").read_text().split()
        assert sorted(executions) == [
            "shop.admin",
            "shop.backoffice",
            "shop.mixins",
            "shop.models",
            "stock.models",
        ]
        result.stdout.fnmatch_lines(["Overall: 3/3 killed (100.0%)*"])
        assert result.ret == 0
