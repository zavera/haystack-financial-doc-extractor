from enum import Enum


class SectionKey(str, Enum):
    HHA_INCOME = "HHA_INCOME"
    HHB_INCOME = "HHB_INCOME"
    STUDENT = "STUDENT"
    ASSETS = "ASSETS"
    HOUSEHOLD = "HOUSEHOLD"
    EXPENSES = "EXPENSES"
