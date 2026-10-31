import os
from importlib.metadata import version, PackageNotFoundError


def get_version() -> str:
    """Get version from package metadata or VERSION env var (for Docker)."""
    try:
        pkg_version = version("polarsen")
        if pkg_version:
            return pkg_version
    except PackageNotFoundError:
        pass
    return os.environ.get("VERSION") or "0.0.0"


__version__ = get_version()
