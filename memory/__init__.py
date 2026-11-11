"""Offline-capable experience memory (WP2-10).

Case-based reasoning over past attempts: find missions like this one, start the search where
they ended up. A hint, never a constraint and never evidence (charter addition 3) -- the
verifier judges every attempt from scratch, so a bad hint costs attempts and nothing else.

Not a flight computer, not a knowledge base, and not RAG. It becomes RAG only if an LLM reads
the retrieved cases as context, which is WP2-11.
"""

from memory import features, warm_start
from memory.store import Store, failure_modes_of

__all__ = ["Store", "failure_modes_of", "features", "warm_start"]
