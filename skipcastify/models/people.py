

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class Person:
    """Represents a person involved with a podcast."""
    name: str
    role: str  # host, guest, producer, etc.
    email: Optional[str] = None
    website: Optional[str] = None
    social_media: Dict[str, str] = field(default_factory=dict)  # platform -> handle