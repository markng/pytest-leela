"""Import hook for in-memory AST mutation — zero file I/O."""

from __future__ import annotations

import ast
import importlib
import importlib.abc
import importlib.machinery
import sys
import types
from typing import Any, Callable

from pytest_leela.models import Mutant

# AST operator classes by name
_OP_CLASSES: dict[str, type] = {
    "Add": ast.Add,
    "Sub": ast.Sub,
    "Mult": ast.Mult,
    "Div": ast.Div,
    "FloorDiv": ast.FloorDiv,
    "Mod": ast.Mod,
    "Pow": ast.Pow,
    "Eq": ast.Eq,
    "NotEq": ast.NotEq,
    "Lt": ast.Lt,
    "LtE": ast.LtE,
    "Gt": ast.Gt,
    "GtE": ast.GtE,
    "Is": ast.Is,
    "IsNot": ast.IsNot,
    "In": ast.In,
    "NotIn": ast.NotIn,
    "And": ast.And,
    "Or": ast.Or,
    "UAdd": ast.UAdd,
    "USub": ast.USub,
    "Not": ast.Not,
}


class MutantApplier(ast.NodeTransformer):
    """Apply a single mutation to an AST."""

    def __init__(self, mutant: Mutant) -> None:
        self.mutant = mutant
        self.applied = False

    def _matches(self, node: ast.AST) -> bool:
        return (
            hasattr(node, "lineno")
            and node.lineno == self.mutant.point.lineno
            and hasattr(node, "col_offset")
            and node.col_offset == self.mutant.point.col_offset
        )

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        if self._matches(node) and self.mutant.point.node_type == "BinOp":
            op_class = _OP_CLASSES.get(self.mutant.replacement_op)
            if op_class is not None:
                node.op = op_class()
                self.applied = True
        return self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        if self._matches(node) and self.mutant.point.node_type == "Compare":
            op_class = _OP_CLASSES.get(self.mutant.replacement_op)
            if op_class is not None:
                # Replace all comparison ops (single comparison case)
                node.ops = [op_class() for _ in node.ops]
                self.applied = True
        return self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        if self._matches(node) and self.mutant.point.node_type == "BoolOp":
            op_class = _OP_CLASSES.get(self.mutant.replacement_op)
            if op_class is not None:
                node.op = op_class()
                self.applied = True
        return self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        if self._matches(node) and self.mutant.point.node_type == "UnaryOp":
            if self.mutant.replacement_op == "_remove":
                self.applied = True
                return node.operand
            op_class = _OP_CLASSES.get(self.mutant.replacement_op)
            if op_class is not None:
                node.op = op_class()
                self.applied = True
        return self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> ast.AST:
        if self._matches(node) and self.mutant.point.node_type == "Return":
            node = self._mutate_return(node)
        return self.generic_visit(node)

    def _mutate_return(self, node: ast.Return) -> ast.Return:
        replacement = self.mutant.replacement_op

        if replacement == "False":
            node.value = ast.Constant(value=False)
            self.applied = True
        elif replacement == "True":
            node.value = ast.Constant(value=True)
            self.applied = True
        elif replacement == "None":
            node.value = ast.Constant(value=None)
            self.applied = True
        elif replacement == "negate" and node.value is not None:
            node.value = ast.UnaryOp(op=ast.USub(), operand=node.value)
            self.applied = True
        elif replacement == "negate_expr" and node.value is not None:
            node.value = ast.UnaryOp(op=ast.Not(), operand=node.value)
            self.applied = True
        elif replacement == "remove_negation" and isinstance(node.value, ast.UnaryOp):
            node.value = node.value.operand
            self.applied = True
        elif replacement == "empty_str":
            node.value = ast.Constant(value="")
            self.applied = True

        return node


def apply_mutation(source: str, mutant: Mutant) -> tuple[str, bool]:
    """Apply a mutation to source code, return (mutated_source_compiled, was_applied)."""
    tree = ast.parse(source)
    applier = MutantApplier(mutant)
    tree = applier.visit(tree)
    ast.fix_missing_locations(tree)
    return tree, applier.applied


class MutatingLoader(importlib.abc.Loader):
    """Loader that applies a mutation to source before executing."""

    def __init__(self, source: str, mutant: Mutant, filename: str) -> None:
        self.source = source
        self.mutant = mutant
        self.filename = filename

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> None:
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        tree = ast.parse(self.source, filename=self.filename)
        applier = MutantApplier(self.mutant)
        tree = applier.visit(tree)
        ast.fix_missing_locations(tree)
        code = compile(tree, self.filename, "exec")
        exec(code, module.__dict__)


class MutatingFinder(importlib.abc.MetaPathFinder):
    """Meta path finder that intercepts imports of target modules."""

    def __init__(
        self,
        target_modules: dict[str, str],
        mutant: Mutant,
    ) -> None:
        # target_modules: {module_name: source_code}
        self.target_modules = target_modules
        self.mutant = mutant
        self._module_to_file: dict[str, str] = {}
        for mod_name in target_modules:
            self._module_to_file[mod_name] = f"<mutated:{mod_name}>"

    def set_file_paths(self, module_to_file: dict[str, str]) -> None:
        self._module_to_file.update(module_to_file)

    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: Any = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname in self.target_modules:
            filename = self._module_to_file.get(fullname, f"<mutated:{fullname}>")
            loader = MutatingLoader(
                self.target_modules[fullname],
                self.mutant,
                filename,
            )
            return importlib.machinery.ModuleSpec(fullname, loader, origin=filename)
        return None


def install_hook(
    target_modules: dict[str, str],
    mutant: Mutant,
    module_to_file: dict[str, str] | None = None,
) -> MutatingFinder:
    """Install a mutating import hook. Returns the finder for later removal."""
    finder = MutatingFinder(target_modules, mutant)
    if module_to_file:
        finder.set_file_paths(module_to_file)
    sys.meta_path.insert(0, finder)
    return finder


def remove_hook(finder: MutatingFinder) -> None:
    """Remove a previously installed import hook."""
    try:
        sys.meta_path.remove(finder)
    except ValueError:
        pass


def clear_target_modules(module_names: list[str]) -> None:
    """Remove target modules from sys.modules to force re-import."""
    for name in module_names:
        sys.modules.pop(name, None)
        # Also clear submodules
        to_remove = [k for k in sys.modules if k.startswith(name + ".")]
        for k in to_remove:
            del sys.modules[k]
