"""Import the Home-Assistant-free modules without booting the integration package.

`custom_components/ecobee_security/__init__.py` imports Home Assistant, which the unit
suite deliberately does not install: api, model, pkce and const are written to stand
alone, and these tests are what keeps them that way.
"""

import importlib.util
import pathlib
import sys
import types

PACKAGE = "custom_components.ecobee_security"
ROOT = pathlib.Path(__file__).parent.parent / "custom_components" / "ecobee_security"


def _install_stub_package() -> None:
    for name, path in (("custom_components", ROOT.parent), (PACKAGE, ROOT)):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module


def _load(name: str) -> None:
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{name}", ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PACKAGE}.{name}"] = module
    spec.loader.exec_module(module)


_install_stub_package()
for _name in ("const", "model", "pkce", "api"):
    _load(_name)
