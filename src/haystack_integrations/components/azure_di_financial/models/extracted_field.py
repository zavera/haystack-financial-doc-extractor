# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class ExtractedField:
    field_name: str
    extracted_value: Optional[Decimal]
    raw_value: str
    confidence: Decimal
    source_doc_type: str
    source_line_ref: Optional[str]
    section: str

    # populated by DeltaCalculator if a reference value is provided
    reference_value: Optional[Decimal] = None
    delta: Optional[Decimal] = None
    severity: Optional[Severity] = None
