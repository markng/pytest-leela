"""Smoke test for pytest_leela.benchmark."""


def describe_benchmark():
    def it_is_importable():
        from pytest_leela.benchmark import BenchmarkPlugin

        assert BenchmarkPlugin is not None
