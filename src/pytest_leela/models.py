"""Data models for mutation testing."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MutationPoint:
    """A location in source code where a mutation can be applied."""

    file_path: str
    module_name: str
    lineno: int
    col_offset: int
    node_type: str  # "BinOp", "Compare", "BoolOp", "UnaryOp", "Return"
    original_op: str  # "Add", "Lt", "And", "True", etc.
    inferred_type: str | None  # "int", "str", "bool", "Optional[int]", None


@dataclass(frozen=True)
class Mutant:
    """A specific mutation to apply."""

    point: MutationPoint
    replacement_op: str
    mutant_id: int


@dataclass
class MutantResult:
    """Result of testing a single mutant."""

    mutant: Mutant
    killed: bool
    tests_run: int
    killing_test: str | None  # Which test killed it
    time_seconds: float
    test_ids_run: list[str] = field(default_factory=list)  # all tests executed
    killing_tests: list[str] = field(default_factory=list)  # all failing tests


@dataclass
class CoverageMap:
    """Maps source lines to the tests that execute them."""

    line_to_tests: dict[tuple[str, int], set[str]] = field(default_factory=dict)

    def tests_for(self, file_path: str, lineno: int) -> set[str]:
        return self.line_to_tests.get((file_path, lineno), set())

    def add(self, file_path: str, lineno: int, test_id: str) -> None:
        key = (file_path, lineno)
        if key not in self.line_to_tests:
            self.line_to_tests[key] = set()
        self.line_to_tests[key].add(test_id)


@dataclass
class RunResult:
    """Complete result of a mutation testing run."""

    target_files: list[str]
    total_mutants: int
    mutants_tested: int
    mutants_pruned: int  # Removed by type analysis
    results: list[MutantResult]
    wall_time_seconds: float
    coverage_map: CoverageMap | None = None
    target_sources: dict[str, str] = field(default_factory=dict)  # file_path -> source

    @property
    def killed(self) -> int:
        return sum(1 for r in self.results if r.killed)

    @property
    def survived(self) -> list[MutantResult]:
        return [r for r in self.results if not r.killed]

    @property
    def mutation_score(self) -> float:
        if self.mutants_tested == 0:
            return 0.0
        return self.killed / self.mutants_tested * 100.0
