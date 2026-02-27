from __future__ import annotations

"""
Compatibility wrapper for parser/company trust scoring.

The implementation already lives in ``src.services.scoring`` and is used by
existing code paths. Re-export it here so parser/antifraud code can depend on a
single namespace without duplicating logic.
"""

from src.services.scoring import ScoreResult, get_score

__all__ = ["ScoreResult", "get_score"]
