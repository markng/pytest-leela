"""The skill is standalone output, not a pytest session."""

from types import SimpleNamespace

import pytest

from pytest_leela.plugin import pytest_cmdline_main
from pytest_leela.skill import SKILL_MARKDOWN


def describe_skill_option():
    @pytest.mark.parametrize("has_test_file", [False, True])
    def it_prints_only_the_skill_without_collecting(pytester, has_test_file):
        if has_test_file:
            pytester.makepyfile(test_tripwire="raise RuntimeError('collection attempted')\n")

        result = pytester.runpytest_subprocess("--skill")

        assert result.ret == 0
        assert result.stdout.str() == SKILL_MARKDOWN.rstrip("\n")
        assert not result.stderr.str()
        frontmatter, body = SKILL_MARKDOWN.removeprefix("---\n").split("\n---\n", 1)
        assert frontmatter.startswith("name: pytest-leela\ndescription:")
        assert "survivor" in body and "--leela" in body

    def it_does_not_intercept_normal_pytest(pytester):
        pytester.makepyfile(test_plain="def test_plain():\n    pass\n")

        result = pytester.runpytest_subprocess("-q")

        assert result.ret == 0
        result.assert_outcomes(passed=1)
        assert "name: pytest-leela" not in result.stdout.str()

    @pytest.mark.parametrize("requested", [False, True])
    def it_only_handles_the_skill_mode_when_selected(capsys, requested):
        def getoption(name, default=None):
            assert name == "skill"
            return requested

        code = pytest_cmdline_main(SimpleNamespace(getoption=getoption))
        if requested:
            assert code is pytest.ExitCode.OK
            assert capsys.readouterr().out == SKILL_MARKDOWN
        else:
            assert code is None
            assert capsys.readouterr().out == ""
