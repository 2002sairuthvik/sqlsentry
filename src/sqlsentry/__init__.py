"""sqlsentry: safety-first, model-agnostic natural language to SQL."""

from .config import Settings
from .engine import SQLSentry
from .errors import SQLSentryError
from .types import Answer, ExecutionResult, Feedback, Generation, ValidationResult

__version__ = "0.1.0"

__all__ = [
    "Answer",
    "ExecutionResult",
    "Feedback",
    "Generation",
    "SQLSentry",
    "SQLSentryError",
    "Settings",
    "ValidationResult",
    "__version__",
]
