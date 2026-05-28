from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


class FakeDistribution:
    def __init__(self, attrs: dict[str, object] | None = None) -> None:
        self.attrs = attrs

    def finalize_options(self) -> None:
        pass


def _import_setup_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    setuptools_module = SimpleNamespace(setup=lambda **kwargs: None)
    dist_module = SimpleNamespace(Distribution=FakeDistribution)
    monkeypatch.setitem(sys.modules, "setuptools", setuptools_module)
    monkeypatch.setitem(sys.modules, "setuptools.dist", dist_module)
    monkeypatch.delitem(sys.modules, "setup_app", raising=False)
    return importlib.import_module("setup_app")


def test_version_reads_pyproject_version(monkeypatch: pytest.MonkeyPatch) -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        expected = tomllib.load(file)["project"]["version"]

    setup_app = _import_setup_app(monkeypatch)

    assert setup_app._version() == expected


def test_py2app_distribution_init_clears_install_requires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_app = _import_setup_app(monkeypatch)

    distribution = setup_app.Py2AppDistribution()

    assert distribution.install_requires == []


def test_py2app_distribution_finalize_options_clears_install_requires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_app = _import_setup_app(monkeypatch)
    distribution = setup_app.Py2AppDistribution()
    distribution.install_requires = ["fake-dep"]

    distribution.finalize_options()

    assert distribution.install_requires == []


def test_py2app_command_factory_returns_subclass_of_imported_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_app = _import_setup_app(monkeypatch)

    class FakePy2App:
        pass

    fake_module = SimpleNamespace(py2app=FakePy2App)

    def fake_import_module(name: str) -> object:
        assert name == "py2app.build_app"
        return fake_module

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    command_class = setup_app._py2app_command()

    assert issubclass(command_class, FakePy2App)
