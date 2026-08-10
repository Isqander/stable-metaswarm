from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_supported_python_version() -> None:
    assert sys.version_info >= (3, 12)


def test_import_comes_from_src_layout() -> None:
    import metaswarm

    assert Path(metaswarm.__file__).resolve() == PROJECT_ROOT / "src/metaswarm/__init__.py"


def test_distribution_metadata_has_no_runtime_dependencies() -> None:
    distribution = metadata.distribution("stable-metaswarm")

    assert distribution.version == "0.1.0"
    assert distribution.requires in (None, [])


def test_distribution_owns_only_metaswarm_top_level_package() -> None:
    distribution = metadata.distribution("stable-metaswarm")

    top_level = distribution.read_text("top_level.txt")
    assert top_level is not None
    assert top_level.splitlines() == ["metaswarm"]
