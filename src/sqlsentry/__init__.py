"""sqlsentry: safety-first, model-agnostic natural language to SQL."""

from .config import Settings
from .errors import SQLSentryError

__version__ = "0.1.0.dev0"

__all__ = ["Settings", "SQLSentryError", "__version__"]
