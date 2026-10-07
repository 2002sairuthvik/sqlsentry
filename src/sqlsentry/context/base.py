from __future__ import annotations

from abc import ABC, abstractmethod

from ..schema.models import SchemaCatalog


class ContextProvider(ABC):
    """Contributes extra prompt context for a question.

    Implementations receive only the policy-filtered catalog and must not return
    information about tables or columns outside it.
    """

    name: str

    @abstractmethod
    def context_for(self, question: str, catalog: SchemaCatalog) -> str | None:
        """Return a block of text to add to the prompt, or None."""
