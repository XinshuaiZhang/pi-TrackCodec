import os

from setuptools import Extension, find_packages, setup


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _native_extensions() -> list[Extension]:
    if _env_truthy("TRACKCODEC_SKIP_NATIVE_BUILD"):
        return []
    try:
        import pybind11
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pybind11 is required to build TrackCodec native extensions. "
            "Install requirements_trackcodec.txt first, or set "
            "TRACKCODEC_SKIP_NATIVE_BUILD=1 when using an ABI-compatible "
            "prebuilt native module already present in the source tree."
        ) from exc

    extra_compile_args = ["/O2", "/std:c++17"] if os.name == "nt" else ["-O3", "-std=c++17"]
    modules = [
        Extension(
            "TrackCodec.production.ms1._cross_scan_speedups",
            ["production/ms1/_cross_scan_speedups.cpp"],
            include_dirs=[pybind11.get_include()],
            language="c++",
            extra_compile_args=extra_compile_args,
        ),
    ]
    if os.name != "nt" or _env_truthy("TRACKCODEC_BUILD_NATIVE_WRITER"):
        modules.append(
            Extension(
                "TrackCodec.production.mzml._native_writer",
                ["production/mzml/_native_writer.cpp"],
                include_dirs=[pybind11.get_include()],
                libraries=["z"] if os.name != "nt" else [],
                language="c++",
                extra_compile_args=extra_compile_args,
            )
        )
    return modules


packages = ["TrackCodec"] + [f"TrackCodec.{name}" for name in find_packages(where=".")]


setup(
    packages=packages,
    package_dir={"TrackCodec": "."},
    ext_modules=_native_extensions(),
    package_data={
        "TrackCodec.production.ms1": ["*.pyd", "*.so"],
        "TrackCodec.production.mzml": ["*.pyd", "*.so"],
    },
)
