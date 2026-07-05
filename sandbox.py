"""
Sandbox — Restricts what hot-loaded extension code can do.

Prevents extensions from:
- Importing dangerous modules (os, subprocess, shutil, socket, etc.)
- Accessing the filesystem outside extensions/
- Making network calls
- Running system commands
- Modifying sys.modules or builtins

Extensions run inside a restricted global scope where dangerous
builtins and imports are blocked.

SECURITY NOTE — known limitation:
The Python-level sandbox blocks naive misuse but cannot fully prevent a
determined escape. The canonical bypass is reaching real builtins via an
allowed module's own __builtins__:

    import json
    real_open = json.decoder.__builtins__["open"]  # bypasses our filter

Mitigations applied here:
  1. Allowed modules are imported fresh into a clean namespace with their
     __builtins__ replaced by our safe dict before being handed to user code.
  2. Attribute traversal to __builtins__, __globals__, __loader__, __spec__
     is not directly blockable at Python level — OS-level isolation (separate
     process, seccomp, minimal filesystem permissions) is the correct defence
     for untrusted code. Extension code here is written by the instances
     themselves, not external users, so the risk profile is lower.
  3. If stronger isolation is needed, replace sandboxed_exec() with
     subprocess execution in a restricted environment.
"""

from __future__ import annotations

import builtins
import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional, Set

logger = logging.getLogger("instance.sandbox")

# ── Blocked Modules ──────────────────────────────────────────────────
# These modules (and sub-modules) are blocked from import inside extensions.
BLOCKED_MODULES: frozenset = frozenset({
    # System / process control
    "os",
    "sys",
    "subprocess",
    "shutil",
    "signal",
    "ctypes",
    "multiprocessing",
    "threading",

    # File system (beyond what we provide)
    "pathlib",
    "glob",
    "tempfile",
    "io",

    # Network
    "socket",
    "http",
    "urllib",
    "requests",
    "aiohttp",
    "httpx",
    "ftplib",
    "smtplib",
    "xmlrpc",

    # Code execution
    "code",
    "codeop",
    "compile",
    "compileall",
    "importlib",
    "runpy",
    "ast",

    # Dangerous introspection
    "inspect",
    "gc",
    "weakref",
    "traceback",

    # Pickle (arbitrary code execution)
    "pickle",
    "shelve",
    "marshal",
})

# ── Blocked Builtins ─────────────────────────────────────────────────
BLOCKED_BUILTINS: frozenset = frozenset({
    "exec",
    "eval",
    "compile",
    "__import__",
    "open",
    "breakpoint",
    "exit",
    "quit",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
    "memoryview",
})

# ── Safe Modules (allowlist) ─────────────────────────────────────────
# Only these modules can be imported inside sandboxed extensions.
ALLOWED_MODULES: frozenset = frozenset({
    "math",
    "random",
    "string",
    "re",
    "json",
    "datetime",
    "time",
    "collections",
    "itertools",
    "functools",
    "operator",
    "decimal",
    "fractions",
    "statistics",
    "hashlib",
    "hmac",
    "base64",
    "copy",
    "enum",
    "dataclasses",
    "typing",
    "abc",
    "textwrap",
    "unicodedata",
    "bisect",
    "heapq",
    "array",
    "struct",
    "pprint",
})


def _is_module_allowed(module_name: str) -> bool:
    """Check if a module is allowed for import inside the sandbox."""
    # Check exact match against allowlist
    if module_name in ALLOWED_MODULES:
        return True
    # Check if it's a sub-module of an allowed module (e.g., collections.abc)
    for allowed in ALLOWED_MODULES:
        if module_name.startswith(allowed + "."):
            return True
    # Check against blocklist
    root_module = module_name.split(".")[0]
    if root_module in BLOCKED_MODULES:
        return False
    # Default: block anything not explicitly allowed
    return False


def _make_safe_import(project_root: Path, safe_builtins: Dict[str, Any]):
    """
    Create a restricted __import__ function for the sandbox.

    After importing an allowed module, replace its __builtins__ with our
    safe dict. This closes the escape path where sandboxed code reaches
    real builtins via an allowed module's own globals:

        import json
        real_open = json.decoder.__builtins__["open"]  # <- blocked by this fix
    """
    real_import = builtins.__import__

    def safe_import(name: str, *args: Any, **kwargs: Any) -> ModuleType:
        if not _is_module_allowed(name):
            raise ImportError(
                f"🔒 SANDBOX: Import of '{name}' is blocked. "
                f"Allowed modules: {sorted(ALLOWED_MODULES)}"
            )
        mod = real_import(name, *args, **kwargs)
        _scrub_module_builtins(mod, safe_builtins, visited=set())
        return mod

    return safe_import


def _scrub_module_builtins(
    mod: ModuleType,
    safe_builtins: Dict[str, Any],
    visited: set,
) -> None:
    """
    Recursively replace __builtins__ in a module and its already-loaded
    sub-attributes with our safe dict. Prevents escape via
    mod.__builtins__["open"] or mod.submod.__builtins__["open"].
    """
    mod_id = id(mod)
    if mod_id in visited:
        return
    visited.add(mod_id)

    try:
        mod.__builtins__ = safe_builtins  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass

    for attr_name in list(vars(mod).keys()):
        if attr_name.startswith("__"):
            continue
        try:
            attr = getattr(mod, attr_name)
            if isinstance(attr, type(mod)):  # it is a module
                _scrub_module_builtins(attr, safe_builtins, visited)
        except Exception:
            pass


def _make_safe_builtins() -> Dict[str, Any]:
    """Create a restricted builtins dict for the sandbox."""
    safe = {}
    for name in dir(builtins):
        if name in BLOCKED_BUILTINS:
            continue
        safe[name] = getattr(builtins, name)
    # Override print to be a no-op that returns the string (no I/O)
    safe["print"] = lambda *args, **kwargs: str(args)
    return safe


def create_sandbox_globals(
    project_root: Path,
    module_name: str,
) -> Dict[str, Any]:
    """
    Create a restricted globals dict for executing extension code.

    The sandbox:
    - Blocks dangerous imports (os, subprocess, socket, etc.)
    - Blocks dangerous builtins (exec, eval, open, etc.)
    - Only allows a curated set of safe modules
    - Scrubs __builtins__ from imported modules to close the
      module.__builtins__["open"] escape path
    """
    safe_builtins = _make_safe_builtins()
    safe_builtins["__import__"] = _make_safe_import(project_root, safe_builtins)

    sandbox_globals: Dict[str, Any] = {
        "__builtins__": safe_builtins,
        "__name__": module_name,
        "__doc__": None,
        "__package__": module_name.rsplit(".", 1)[0] if "." in module_name else None,
    }

    return sandbox_globals


def sandboxed_exec(
    code: str,
    project_root: Path,
    module_name: str = "__sandbox__",
) -> Dict[str, Any]:
    """
    Execute code in a sandboxed environment.

    Returns the resulting namespace (variables defined by the code).
    Raises ImportError if the code tries to import blocked modules.
    Raises any exceptions the code itself raises.

    Note: best-effort Python-level sandbox. For truly untrusted code,
    use OS-level isolation. See module docstring for details.
    """
    sandbox_globals = create_sandbox_globals(project_root, module_name)
    exec(code, sandbox_globals)  # noqa: S102 — intentional sandboxed exec
    return sandbox_globals
