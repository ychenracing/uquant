"""Include the original resource bytes required by installed production identities."""

import json
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        registry_path = "benchmarks/source_surface_registry.json"
        registry = json.loads((root / registry_path).read_bytes())
        resources = {registry_path}
        for surface in registry["surfaces"]:
            if all(path.startswith("uquant/") for path in surface["source_paths"]):
                resources.update(
                    path for path in surface["resource_paths"] if not path.startswith("uquant/")
                )
        destination = Path(self.build_lib) / "uquant" / "_source"
        for relative in sorted(resources):
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
                raise ValueError(f"unsafe source identity resource: {relative}")
            source = root / path
            if any(part.is_symlink() for part in (source, *source.parents)):
                raise ValueError(f"unsafe source identity resource: {relative}")
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())


setup(cmdclass={"build_py": BuildPy})
