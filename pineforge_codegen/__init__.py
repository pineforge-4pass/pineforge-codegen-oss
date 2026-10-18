"""Compatibility import for the renamed :mod:`tunaxis_codegen` package.

Only this forwarding package belongs to the pineforge-codegen distribution.
The implementation and every module object belong to tunaxis-codegen.
"""

import importlib
import importlib.abc
import importlib.util
import sys
import warnings


class _AliasLoader(importlib.abc.Loader):
    """Return the canonical module without executing its code a second time."""

    def __init__(self, canonical_spec):
        self._spec = canonical_spec

    def create_module(self, spec):
        return importlib.import_module(self._spec.name)

    def exec_module(self, module):
        # create_module already imported the canonical module. Import machinery
        # registers that same object under the alias and on its parent package.
        pass

    def get_code(self, fullname):
        # runpy / python -m need code under the requested name, whereas a
        # SourceFileLoader accepts only its canonical name. Relative imports
        # in that code still pass through the alias finder.
        return self._spec.loader.get_code(self._spec.name)


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith("pineforge_codegen."):
            return None
        canonical = "tunaxis_codegen" + fullname[len("pineforge_codegen"):]
        spec = importlib.util.find_spec(canonical)
        if spec is None:
            return None
        return importlib.util.spec_from_loader(
            fullname,
            _AliasLoader(spec),
            origin=spec.origin,
            is_package=spec.submodule_search_locations is not None,
        )


warnings.warn(
    "pineforge_codegen / pineforge-codegen was renamed; import tunaxis_codegen "
    "and install tunaxis-codegen instead.",
    DeprecationWarning,
    stacklevel=2,
)

_canonical = importlib.import_module("tunaxis_codegen")
sys.meta_path.insert(0, _AliasFinder())

# The canonical package eagerly imports part of its tree. Seed those aliases
# too, so Python never loads one of their source files under the old name.
for _name, _module in tuple(sys.modules.items()):
    if _name == "tunaxis_codegen" or _name.startswith("tunaxis_codegen."):
        sys.modules["pineforge_codegen" + _name[len("tunaxis_codegen"):]] = _module
