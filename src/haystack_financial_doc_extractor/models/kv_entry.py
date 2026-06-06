from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class KvEntry:
    key: str
    value: str
    confidence: Decimal
