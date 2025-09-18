import importlib, pkgutil, pathlib

def load_all():
    """Import every .py in this package so all db.Model classes register on metadata."""
    pkg = __name__
    pkg_path = pathlib.Path(__file__).parent
    for _, module_name, _ in pkgutil.iter_modules([str(pkg_path)]):
        if module_name.startswith("_"):    # skip private
            continue
        importlib.import_module(f"{pkg}.{module_name}")
