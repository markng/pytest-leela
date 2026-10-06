"""End-to-end: ``pytest --leela`` in a subprocess against throwaway projects."""

import textwrap

import pytest

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
                "*pytest exited with USAGE_ERROR (test_calc.py: E   RuntimeError:"
                " limit changed)",
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
                "leela: aborted: tests do not pass with no mutation applied "
                "(failed: test_calc.py::test_double_once)*"
            ]
        )
        assert "Overall:" not in result.stdout.str()


def describe_clean_project():
    def it_kills_every_mutant_and_passes(pytester):
        pytester.makepyfile(calc=_DOUBLE, test_calc=_DOUBLE_TEST)

        result = _leela(pytester)

        assert result.ret == 0
        result.stdout.fnmatch_lines(["Overall: *killed (100.0%)*"])


def _django_project(pytester, total_check):
    pytest.importorskip("pytest_django")
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
                "shop",
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
    (shop / "mixins.py").write_text("class PricedMixin:\n    currency = 'USD'\n")
    (shop / "models.py").write_text(
        "from django.db import models\n\n"
        "from shop.mixins import PricedMixin\n\n\n"
        "class Item(PricedMixin, models.Model):\n"
        "    name = models.CharField(max_length=20)\n"
    )
    (shop / "admin.py").write_text(
        "from django.contrib import admin\n\n"
        "from shop.models import Item\n\n"
        "admin.site.register(Item)\n"
    )
    (shop / "pricing.py").write_text("def total(a, b):\n    return a + b\n")
    tests = pytester.mkdir("tests")
    (tests / "test_shop.py").write_text(
        "import pytest\n\n"
        "from shop import admin as shop_admin\n"
        "from shop.mixins import PricedMixin\n"
        "from shop.models import Item\n"
        "from shop.pricing import total\n\n\n"
        "@pytest.mark.django_db\n"
        "def test_item_round_trips_through_the_database():\n"
        "    Item.objects.create(name='pen')\n"
        "    assert Item.objects.count() == 1\n"
        # Inner runs only execute tests that cover the target, so the
        # identity checks must live in the covering test.
        "    assert issubclass(Item, PricedMixin)\n"
        "    assert shop_admin.admin.site.is_registered(Item)\n"
        f"    assert {total_check}\n"
    )
    return pytester


def describe_django_project():
    def it_runs_database_tests_inside_the_inner_session(pytester):
        """total(0, 0) cannot tell + from -, so Add -> Sub must survive.

        With pytest < 9.1 (huub's stack) 0.8.0 reported it killed: the DB
        test errored in setup inside every inner run.
        """
        django_project = _django_project(pytester, "total(0, 0) == 0")
        result = django_project.runpytest_subprocess(
            "-p", "no:cacheprovider", "--leela", "--target", "shop/pricing.py"
        )

        output = result.stdout.str()
        assert "leela: aborted" not in output
        result.stdout.fnmatch_lines(["*line 2: + * SURVIVED"])
        assert result.ret == 1

    def it_keeps_models_admin_and_their_mixin_loaded(pytester):
        """No AlreadyRegistered on re-import, no split mixin identity.

        Re-executing shop/admin.py raises AlreadyRegistered at collection,
        which 0.8.0 scored as a survivor; here it would fail the baseline.
        """
        django_project = _django_project(pytester, "total(2, 3) == 5")
        result = django_project.runpytest_subprocess(
            "-p", "no:cacheprovider", "--leela", "--target", "shop/pricing.py"
        )

        output = result.stdout.str()
        assert "AlreadyRegistered" not in output
        assert "leela: aborted" not in output
        assert "ERROR" not in output
        result.stdout.fnmatch_lines(["Overall: 3/3 killed (100.0%)*"])
        assert result.ret == 0
