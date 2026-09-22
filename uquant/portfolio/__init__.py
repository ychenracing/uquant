"""The single, statically declared portfolio allocator."""

from ..portfolio_core import current_weights, effective_n
from .allocator import PortfolioAllocator

__all__ = ("PortfolioAllocator", "current_weights", "effective_n")
