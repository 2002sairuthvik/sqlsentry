"""Context providers: extension point for extra knowledge given to the model.

v0.1 ships the built-in sources (introspected schema, sample values, glossary, examples)
directly in the engine. Future providers (query history, data catalogs, docs) implement
:class:`ContextProvider` and contribute text that is policy-filtered like everything else.
"""

from .base import ContextProvider

__all__ = ["ContextProvider"]
