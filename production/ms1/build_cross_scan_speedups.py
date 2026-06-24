from __future__ import annotations

import os
from pathlib import Path
from setuptools import Extension, find_packages, setup
import pybind11


ROOT = Path(__file__).resolve().parent
TRACKCODEC_ROOT = ROOT.parents[1]


def main() -> None:
    os.chdir(TRACKCODEC_ROOT)
    ext_modules = [
        Extension(
            "TrackCodec.production.ms1._cross_scan_speedups",
            [str(ROOT / "_cross_scan_speedups.cpp")],
            include_dirs=[pybind11.get_include()],
            language="c++",
            extra_compile_args=["-O3", "-std=c++17"],
        )
    ]
    packages = ["TrackCodec"] + [f"TrackCodec.{name}" for name in find_packages(where=".")]
    setup(
        name="TrackCodec",
        packages=packages,
        package_dir={"TrackCodec": "."},
        ext_modules=ext_modules,
        script_args=["build_ext", "--inplace"],
    )


if __name__ == "__main__":
    main()
