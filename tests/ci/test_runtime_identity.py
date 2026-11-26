"""Executable equality is not an opaque provider image-build label."""
from ci.mutations import environment, runtime


def test_image_provenance_is_separate_from_exact_executable_identity(monkeypatch):
    monkeypatch.setenv("ImageVersion", "image-a")
    first = runtime(); before = environment()
    monkeypatch.setenv("ImageVersion", "image-b")
    second = runtime(); after = environment()
    assert first == second
    assert before != after
    assert len(first["python_executable_sha256"]) == 64
    assert len(first["libpython_sha256"]) == 64
    assert first["soabi"]
    assert "image_version" not in first
