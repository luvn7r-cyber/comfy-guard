"""comfy-guard: Per-plugin dependency rollback & fast cloud GPU sync for ComfyUI."""
__version__ = "0.1.0"
from .core import ComfyGuard

__all__ = ["ComfyGuard", "__version__"]
