# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class KvEntry:
    key: str
    value: str
    confidence: Decimal
