"""Private exec worker: no project imports until the parent sends a request."""

import json
import sys
import threading
from dataclasses import asdict
from typing import Any


def execute(request: dict[str, Any]) -> dict[str, Any]:
    # Reproduce the caller's import paths before importing the runner. This
    # includes pytest's configured pythonpath and editable source locations.
    sys.path[:] = request["path"]
    # Python <=3.13 -S omits venv prefix setup along with site hooks. Restore
    # the caller's interpreter identity without executing uncontained .pth.
    for name, value in request["prefixes"].items():
        setattr(sys, name, value)
    from pytest_leela.import_hook import install_hook
    from pytest_leela.models import Mutant, MutationPoint
    from pytest_leela.runner import InnerSession, ProjectModuleScope

    mutation = request["mutation"]
    finder = None
    if mutation is not None:
        mutant = Mutant(
            MutationPoint(**mutation["point"]),
            mutation["replacement_op"],
            mutation["mutant_id"],
        )
        finder = install_hook(request["sources"], mutant, request["files"])
    session = InnerSession(
        finder, request["test_ids"], request["test_dir"],
        ProjectModuleScope(request["cwd"]),
        exitfirst=request["exitfirst"],
    ).run()
    collector = vars(session.collector).copy()
    collector["failure_evidence"] = [
        asdict(evidence) for evidence in session.collector.failure_evidence.values()
    ]
    collector["conftest_failure_ids"] = sorted(session.collector.conftest_failure_ids)
    collector["exit_ids"] = sorted(session.collector.exit_ids)
    root = session.collector._rootpath
    collector["_rootpath"] = None if root is None else str(root)
    return {
        "collector": collector,
        "exit_code": int(session.exit_code),
        "crash": session.crash,
        "import_errors": session.import_errors,
        "import_failures": [] if finder is None else finder._import_failures,
    }


def main() -> None:
    # A single JSON message, not pickle: test code must not gain a new route
    # to execute serialized objects in the publication/SQLite-owning parent.
    request = json.loads(sys.stdin.buffer.read())
    response = execute(request)
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()
    # Keep the group leader owned and unreaped until the parent has killed
    # the entire group, including any subprocesses left by the tests.
    threading.Event().wait()


if __name__ == "__main__":
    main()
