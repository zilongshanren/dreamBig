"""Versioned prompt templates for LLM calls.

Every prompt used in production lives as a PromptTemplate instance so we
can track version bumps alongside output schema changes. When you modify
a template, increment `version` — downstream consumers may key their
cached outputs by (template name, template version).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptTemplate:
    """A named, versioned prompt with a system message and user template.

    The user_template uses Python str.format syntax — call `.render(**kwargs)`
    to produce an OpenAI-format messages list.
    """

    name: str
    version: str
    system: str
    user_template: str

    def render(self, **kwargs: object) -> list[dict[str, str]]:
        """Render the template to an OpenAI-format messages list."""
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user_template.format(**kwargs)},
        ]

    def __repr__(self) -> str:
        return f"PromptTemplate(name={self.name!r}, version={self.version!r})"


__all__ = ["PromptTemplate"]
