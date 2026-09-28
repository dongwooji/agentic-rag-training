"""Grounded final-answer, abstention, and failure response layer."""

from .contracts import FinalResponse
from .generator import FinalResponseLayer

__all__ = ["FinalResponse", "FinalResponseLayer"]
