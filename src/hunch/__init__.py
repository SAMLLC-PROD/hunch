"""Hunch: give your AI a hunch about which of your documents matter."""
__version__ = "0.1.1"
DEFAULT_HTTP_PORT = 8741

from .project import Project, HunchError  # noqa: E402,F401
from .resolver import resolve, ContextPack  # noqa: E402,F401
