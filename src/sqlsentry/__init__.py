"""sqlsentry: safety-first, model-agnostic natural language to SQL."""

from .config import Settings
from .engine import SQLSentry
from .errors import SQLSentryError
from .types import ExecutionResult, Feedback, Generation, ValidationResult

__version__ = "0.1.0.dev0"

__all__ = [
    "ExecutionResult",
    "Feedback",
    "Generation",
    "SQLSentry",
    "SQLSentryError",
    "Settings",
    "ValidationResult",
    "__version__",
]
