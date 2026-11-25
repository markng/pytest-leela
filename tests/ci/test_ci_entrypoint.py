"""Actual workflow CLI on real native toy mutants; no mocked verdicts."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import select
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]


class EntryPoint(unittest.TestCase):
    def test_real_plan_shards_serial_and_aggregate(self):
        with tempfile.TemporaryDirectory(prefix="leela-ci-entry-") as temporary:
            parent = Path(temporary); root = parent / "source"; root.mkdir()
            (root / "subject.py").write_text("def sign(x: int) -> bool:\n    return x > 0\n")
            (root / "test_subject.py").write_text("from subject import sign\ndef test_sign():\n    for x in [-1, 0, 1]:\n        assert sign(x) == (x > 0)\n")
            public = {k: os.environ[k] for k in ["PATH", "LANG", "LC_ALL", "SYSTEMROOT"] if k in os.environ}
            def environment(cwd):
                for n in ["home", "tmp"]: (cwd / n).mkdir(exist_ok=True)
                return public | {"HOME": str(cwd / "home"), "TMPDIR": str(cwd / "tmp"), "PYTHONPATH": str(REPO), "LEELA_CI_FIXTURE": "1", "LEELA_SERIAL_PARITY": "true", "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"}
            def git(*args):
                subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "user.name=Synthetic fixture", "-c", "user.email=fixture@example.invalid", *args], cwd=root, env=environment(root), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git("init", "-q"); git("add", "subject.py", "test_subject.py"); git("commit", "-qm", "Synthetic native CI fixture")
            prefix = [sys.executable, "-m", "ci.mutations"]
            def run(*args, ok=True, cwd=root):
                result = subprocess.run([*prefix, *args], cwd=cwd, env=environment(cwd), capture_output=True, text=True, timeout=45)
                if ok: self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else: self.assertNotEqual(result.returncode, 0)
                return result
            run("plan", "--target", "subject.py", "--output", "plan")
            plan_file = root / "plan/plan.json"; plan = json.loads(plan_file.read_text())
            self.assertGreaterEqual(len(plan["mutants"]), 2)
            self.assertEqual(set(sum(plan["shards"], [])), set(range(len(plan["mutants"]))))
            run("dependencies", "--plan", str(plan_file), "--output", "pins.txt")
            workspaces = []
            for name in ["w0", "w1", "serial"]:
                work = parent / name
                shutil.copytree(root, work, ignore=shutil.ignore_patterns("plan", "home", "tmp", "pins.txt", "__pycache__", ".pytest_cache"))
                workspaces.append(work)
            children = []
            for index, work in enumerate(workspaces[:2]):
                args = [*prefix, "run", "--target", "subject.py", "--plan", str(plan_file), "--index", str(index), "--output", "out/result.json", "--wait-stdin"]
                children.append(subprocess.Popen(args, cwd=work, env=environment(work), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            try:
                for child in children:
                    self.assertTrue(select.select([child.stdout], [], [], 45)[0], "fixture did not become ready")
                    ready = child.stdout.readline()
                    self.assertEqual(json.loads(ready), {"phase": "ready"})
                for child in children:
                    child.stdin.write("GO\n"); child.stdin.flush()
                for child in children:
                    out, err = child.communicate(timeout=45)
                    self.assertEqual(child.returncode, 0, out + err)
            finally:
                for child in children:
                    if child.poll() is None: child.kill(); child.wait()
            serial = workspaces[2]
            run("run", "--target", "subject.py", "--plan", str(plan_file), "--index", "0", "--count", "1", "--output", "out/result.json", cwd=serial)
            for index, work in enumerate(workspaces[:2]):
                dest = root / "shards" / str(index); dest.mkdir(parents=True)
                shutil.copyfile(work / "out/result.json", dest / "result.json")
            run("aggregate", "--target", "subject.py", "--plan", str(plan_file), "--results", "shards", "--serial", str(serial / "out"), "--output", "aggregate.json")
            result = json.loads((root / "aggregate.json").read_text())
            self.assertEqual(result["ids"], list(range(len(plan["mutants"]))))
            self.assertTrue(result["serial_parity"])
            self.assertGreater(result["actual_mutant_utc_overlap_seconds"], 0)
            (root / "shards/1/result.json").rename(root / "lost.json")
            run("aggregate", "--target", "subject.py", "--plan", str(plan_file), "--results", "shards", "--serial", str(serial / "out"), "--output", "must-not-exist.json", ok=False)
            self.assertFalse((root / "must-not-exist.json").exists())
            # Optional retained proof for the locked Ubuntu smoke step. Do not
            # leave native verdicts only in a temporary test directory.
            evidence = os.environ.get("LEELA_CI_EVIDENCE_DIR")
            if evidence:
                import pytest
                dest = Path(evidence); dest.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(plan_file, dest / "plan.json")
                shutil.copyfile(root / "aggregate.json", dest / "aggregate.json")
                for label, work in zip(["shard-0", "shard-1", "serial"], workspaces):
                    shutil.copyfile(work / "out/result.json", dest / (label + ".json"))
                serial_result = json.loads((serial / "out/result.json").read_text())
                proof = {"schema": "leela.locked-smoke/v1", "scope": "Real native toy fixture through actual CI entry point, NOT full release proof", "source_sha": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(), "lock_sha256": hashlib.sha256((REPO / "uv.lock").read_bytes()).hexdigest(), "python": sys.version, "python_executable": sys.executable, "pytest_version": pytest.__version__, "pytest_file": pytest.__file__, "test": self.id(), "platform": sys.platform, "image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion"), "native_verdicts": [{k: r[k] for k in ["id", "site", "status", "timeout", "error", "killing_tests"]} for r in serial_result["results"]], "serial_parity": result["serial_parity"], "actual_mutant_utc_overlap_seconds": result["actual_mutant_utc_overlap_seconds"], "missing_shard_refused": True}
                (dest / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")


if __name__ == "__main__": unittest.main()
