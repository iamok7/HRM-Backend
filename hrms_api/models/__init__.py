# hrms_api/models/__init__.py
import importlib
import pkgutil
import pathlib

# Optional: tweak if you ever add folders we should skip beneath models/
_EXCLUDE_PREFIXES = (
    "hrms_api.models.__pycache__",
    "hrms_api.models.tests",
    "hrms_api.models.migrations",
)

def load_all():
    """Import every .py in this package (and subpackages) so all models register."""
    pkg = __name__
    pkg_path = pathlib.Path(__file__).parent

    def _walk_and_import(pkg_name: str, path: pathlib.Path):
        for mod in pkgutil.iter_modules([str(path)]):
            full = f"{pkg_name}.{mod.name}"
            importlib.import_module(full)
            # Recurse into subpackages
            subpath = path / mod.name
            if subpath.is_dir() and (subpath / "__init__.py").exists():
                _walk_and_import(full, subpath)

    _walk_and_import(pkg, pkg_path)
