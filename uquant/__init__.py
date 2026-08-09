"""uquant: a causal A-share technology-sector decision system."""

from .config import DEFAULT_CONFIG, SystemConfig
from .engine import ProductionEngine

__all__ = ["DEFAULT_CONFIG", "ProductionEngine", "SystemConfig"]
__version__ = "1.0.0"
