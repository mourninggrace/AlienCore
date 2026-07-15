"""
Regression test for a class of crash bug: deferred Tk ``after(0, lambda: ...)``
callbacks that reference an exception variable bound by ``except ... as e:``.

Python deletes the ``except`` target at the end of the except block (PEP 3110),
so any lambda scheduled with ``after()`` that closes over a *free* ``e`` raises
``NameError`` when Tk later runs it — which happens precisely on the hardware
detection / driver / services error path. The fix is to bind the exception into
the lambda's default args (``lambda e=e: ...``).

This test scans the GUI source for the offending pattern so the bug stays dead.
"""
import ast
import os

GUI_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gui")


def _free_exception_refs(tree, exc_names):
    """Yield (lineno, name) for lambdas that reference an exc name WITHOUT
    rebinding it as a parameter/default."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Lambda):
            continue
        # names rebound as lambda params (incl. defaults like ``e=e``) are safe
        bound = {a.arg for a in node.args.args}
        bound |= {a.arg for a in node.args.kwonlyargs}
        if node.args.vararg:
            bound.add(node.args.vararg.arg)
        if node.args.kwarg:
            bound.add(node.args.kwarg.arg)
        for sub in ast.walk(node.body):
            if (isinstance(sub, ast.Name)
                    and sub.id in exc_names
                    and sub.id not in bound):
                yield sub.lineno, sub.id


def _exception_handler_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def test_no_lambda_closes_over_except_variable():
    offenders = []
    for fname in os.listdir(GUI_DIR):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(GUI_DIR, fname)
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src, filename=path)
        exc_names = _exception_handler_names(tree)
        if not exc_names:
            continue
        for lineno, name in _free_exception_refs(tree, exc_names):
            offenders.append(f"{fname}:{lineno} lambda closes over '{name}'")
    assert not offenders, (
        "Deferred lambdas reference an except-bound variable that Python "
        "deletes (NameError at callback time). Bind it as a default "
        "(lambda e=e: ...):\n  " + "\n  ".join(offenders))
