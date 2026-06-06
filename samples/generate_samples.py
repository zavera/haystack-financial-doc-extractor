"""
Fills official IRS PDF templates with realistic fake data.

All names, SSNs, EINs, and dollar amounts are entirely fictional.

Run:
    ../.venv/bin/python generate_samples.py
"""
from pypdf import PdfReader, PdfWriter


def fill(template: str, output: str, fields: dict):
    reader = PdfReader(template)
    writer = PdfWriter()
    writer.append(reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, fields, auto_regenerate=False)
    with open(output, "wb") as f:
        writer.write(f)
    print(f"  ✅  {output}")


# ---------------------------------------------------------------------------
# Form 1040
# f1_01 = first name, f1_02 = last name, f1_03 = SSN (spouse), f1_04 = SSN
# f1_13 = wages (line 1a), f1_28 = AGI area, numeric lines follow in order
# ---------------------------------------------------------------------------
def generate_1040():
    fill("f1040.pdf", "f1040_filled.pdf", {
        # Name / identity
        "topmostSubform[0].Page1[0].f1_01[0]":  "James",
        "topmostSubform[0].Page1[0].f1_02[0]":  "Harrington",
        "topmostSubform[0].Page1[0].f1_04[0]":  "XXX-XX-1234",
        # Address
        "topmostSubform[0].Page1[0].Address_ReadOrder[0].f1_20[0]": "742 Evergreen Terrace",
        "topmostSubform[0].Page1[0].Address_ReadOrder[0].f1_21[0]": "Springfield",
        "topmostSubform[0].Page1[0].Address_ReadOrder[0].f1_22[0]": "IL",
        "topmostSubform[0].Page1[0].Address_ReadOrder[0].f1_23[0]": "62701",
        # Income lines (page 1)
        "topmostSubform[0].Page1[0].f1_13[0]":  "82000",    # Line 1a wages
        "topmostSubform[0].Page1[0].f1_25[0]":  "1200",     # Line 2b ordinary dividends
        "topmostSubform[0].Page1[0].f1_26[0]":  "83200",    # Line 9  total income
        # Page 2 — AGI, deductions, tax
        "topmostSubform[0].Page2[0].f2_01[0]":  "83200",    # Line 11 AGI
        "topmostSubform[0].Page2[0].f2_02[0]":  "13850",    # Line 12 standard deduction
        "topmostSubform[0].Page2[0].f2_05[0]":  "69350",    # Line 15 taxable income
        "topmostSubform[0].Page2[0].f2_06[0]":  "11500",    # Line 16 tax
        "topmostSubform[0].Page2[0].f2_14[0]":  "11500",    # Line 24 total tax
        "topmostSubform[0].Page2[0].f2_15[0]":  "13200",    # Line 25a withholding
        "topmostSubform[0].Page2[0].f2_19[0]":  "13200",    # Line 33 total payments
        "topmostSubform[0].Page2[0].f2_20[0]":  "1700",     # Line 35a refund
    })


# ---------------------------------------------------------------------------
# W-2  (Copy A fields — CopyA prefix)
# f1_01 = SSN, f1_02 = EIN, f1_03 = employer name, f1_04 = employer address
# f1_05/06 = employee name, f1_09 = box1 wages, f1_10 = box2 withheld, etc.
# ---------------------------------------------------------------------------
def generate_w2():
    fill("fw2.pdf", "fw2_filled.pdf", {
        "topmostSubform[0].CopyA[0].BoxA_ReadOrder[0].f1_01[0]":              "XXX-XX-1234",
        "topmostSubform[0].CopyA[0].Col_Left[0].f1_02[0]":                    "12-3456789",
        "topmostSubform[0].CopyA[0].Col_Left[0].f1_03[0]":                    "Acme Corporation",
        "topmostSubform[0].CopyA[0].Col_Left[0].f1_04[0]":                    "100 Industrial Blvd, Chicago IL 60601",
        "topmostSubform[0].CopyA[0].Col_Left[0].FirstName_ReadOrder[0].f1_05[0]":  "James",
        "topmostSubform[0].CopyA[0].Col_Left[0].LastName_ReadOrder[0].f1_06[0]":   "Harrington",
        "topmostSubform[0].CopyA[0].Col_Left[0].f1_07[0]":                    "742 Evergreen Terrace Springfield IL 62701",
        # Right column — boxes 1-12
        "topmostSubform[0].CopyA[0].Col_Right[0].Box1_ReadOrder[0].f1_09[0]": "82000",    # Box 1 wages
        "topmostSubform[0].CopyA[0].Col_Right[0].f1_10[0]":                   "13200",    # Box 2 federal withheld
        "topmostSubform[0].CopyA[0].Col_Right[0].Box3_ReadOrder[0].f1_11[0]": "82000",    # Box 3 SS wages
        "topmostSubform[0].CopyA[0].Col_Right[0].f1_12[0]":                   "5084",     # Box 4 SS tax
        "topmostSubform[0].CopyA[0].Col_Right[0].Box5_ReadOrder[0].f1_13[0]": "82000",    # Box 5 Medicare wages
        "topmostSubform[0].CopyA[0].Col_Right[0].f1_14[0]":                   "1189",     # Box 6 Medicare tax
        "topmostSubform[0].CopyA[0].Col_Right[0].Box7_ReadOrder[0].f1_15[0]": "0",        # Box 7 SS tips
        "topmostSubform[0].CopyA[0].Col_Right[0].Box10_ReadOrder[0].f1_18[0]":"0",        # Box 10 dependent care
        # Box 12 — 401k
        "topmostSubform[0].CopyA[0].Col_Right[0].Line12_ReadOrder[0].f1_20[0]": "D",      # code
        "topmostSubform[0].CopyA[0].Col_Right[0].Line12_ReadOrder[0].f1_21[0]": "4100",   # amount
        # State — box 15-17
        "topmostSubform[0].CopyA[0].Col_Left[0].f1_08[0]":  "IL / IL-987654",
    })


# ---------------------------------------------------------------------------
# Schedule C
# f1_1 = taxpayer name, f1_2 = business name, f1_3 = EIN, f1_5 = address
# income section: f1_10=gross receipts, f1_14=gross profit, f1_15=gross income
# expense section starts around f1_17
# ---------------------------------------------------------------------------
def generate_schedule_c():
    fill("f1040sc.pdf", "f1040sc_filled.pdf", {
        "topmostSubform[0].Page1[0].f1_1[0]":   "James Harrington",         # taxpayer name
        "topmostSubform[0].Page1[0].f1_2[0]":   "Freelance Software Consulting",
        "topmostSubform[0].Page1[0].f1_3[0]":   "98-7654321",               # EIN
        "topmostSubform[0].Page1[0].f1_5[0]":   "742 Evergreen Terrace, Springfield IL 62701",
        # Income
        "topmostSubform[0].Page1[0].f1_10[0]":  "45000",   # Line 1 gross receipts
        "topmostSubform[0].Page1[0].f1_13[0]":  "45000",   # Line 3 gross profit
        "topmostSubform[0].Page1[0].f1_14[0]":  "500",     # Line 6 other income
        "topmostSubform[0].Page1[0].f1_15[0]":  "45500",   # Line 7 gross income
        # Expenses
        "topmostSubform[0].Page1[0].Lines8-17[0].f1_17[0]": "800",    # advertising
        "topmostSubform[0].Page1[0].Lines8-17[0].f1_19[0]": "1200",   # car/truck
        "topmostSubform[0].Page1[0].Lines8-17[0].f1_21[0]": "600",    # insurance
        "topmostSubform[0].Page1[0].Lines8-17[0].f1_22[0]": "1500",   # legal/professional
        "topmostSubform[0].Page1[0].Lines8-17[0].f1_23[0]": "400",    # office
        "topmostSubform[0].Page1[0].Lines8-17[0].f1_25[0]": "2400",   # rent
        "topmostSubform[0].Page1[0].Lines8-17[0].f1_26[0]": "350",    # supplies
        "topmostSubform[0].Page1[0].Lines8-17[0].f1_27[0]": "250",    # taxes/licenses
        "topmostSubform[0].Page1[0].f1_28[0]":  "600",     # utilities
        "topmostSubform[0].Page1[0].f1_30[0]":  "8100",    # total expenses
        "topmostSubform[0].Page1[0].f1_31[0]":  "37400",   # net profit
    })


# ---------------------------------------------------------------------------
# Schedule K-1 (Form 1065)
# f1_1/f1_2 = tax year, f1_6 = partnership EIN, f1_7 = partnership name
# f1_9 = partner SSN, f1_10 = partner name
# ---------------------------------------------------------------------------
def generate_k1():
    fill("f1065sk1.pdf", "f1065sk1_filled.pdf", {
        # Header
        "topmostSubform[0].Page1[0].Pg1Header[0].ForCalendarYear[0].f1_1[0]": "2023",
        "topmostSubform[0].Page1[0].Pg1Header[0].ForCalendarYear[0].f1_2[0]": "2023",
        # Partnership info
        "topmostSubform[0].Page1[0].LeftCol[0].f1_6[0]":  "55-1234567",           # EIN
        "topmostSubform[0].Page1[0].LeftCol[0].f1_7[0]":  "Harrington & Associates LP",
        "topmostSubform[0].Page1[0].LeftCol[0].f1_8[0]":  "500 Michigan Ave, Chicago IL 60611",
        # Partner info
        "topmostSubform[0].Page1[0].LeftCol[0].f1_9[0]":  "XXX-XX-1234",
        "topmostSubform[0].Page1[0].LeftCol[0].f1_10[0]": "James Harrington",
        "topmostSubform[0].Page1[0].LeftCol[0].f1_11[0]": "742 Evergreen Terrace, Springfield IL 62701",
        # Profit/loss/capital sharing %
        "topmostSubform[0].Page1[0].LeftCol[0].LineJTable[0].Profit[0].f1_14[0]":  "40",
        "topmostSubform[0].Page1[0].LeftCol[0].LineJTable[0].Profit[0].f1_15[0]":  "40",
        "topmostSubform[0].Page1[0].LeftCol[0].LineJTable[0].Loss[0].f1_16[0]":    "40",
        "topmostSubform[0].Page1[0].LeftCol[0].LineJTable[0].Loss[0].f1_17[0]":    "40",
        "topmostSubform[0].Page1[0].LeftCol[0].LineJTable[0].Capital[0].f1_18[0]": "40",
        "topmostSubform[0].Page1[0].LeftCol[0].LineJTable[0].Capital[0].f1_19[0]": "40",
        # Income boxes (right column)
        "topmostSubform[0].Page1[0].RtCol[0].f1_30[0]": "18400",   # Box 1 ordinary income
        "topmostSubform[0].Page1[0].RtCol[0].f1_32[0]": "0",       # Box 2 net rental
        "topmostSubform[0].Page1[0].RtCol[0].f1_36[0]": "320",     # Box 5 interest income
    })


# ---------------------------------------------------------------------------
# Schedule E — Supplemental Income (rental property, page 1)
# f1_1 = taxpayer name, f1_2 = SSN
# Table_Line1a = property addresses (RowA/B/C)
# Table_Income = rents received, royalties
# Table_Expenses = advertising, auto, insurance, mortgage interest, taxes, etc.
# ---------------------------------------------------------------------------
def generate_schedule_e():
    fill("f1040se.pdf", "f1040se_filled.pdf", {
        # Header
        "topmostSubform[0].Page1[0].f1_1[0]": "James Harrington",
        "topmostSubform[0].Page1[0].f1_2[0]": "XXX-XX-1234",
        # Property A address
        "topmostSubform[0].Page1[0].Table_Line1a[0].RowA[0].f1_3[0]": "88 Maple Street, Springfield IL 62702",
        # Property type (1=Single Family)
        "topmostSubform[0].Page1[0].Table_Line1b[0].RowA[0].f1_6[0]": "1",
        # Days rented / personal use
        "topmostSubform[0].Page1[0].Table_Line2[0].RowA[0].f1_9[0]":  "280",   # days rented
        "topmostSubform[0].Page1[0].Table_Line2[0].RowA[0].f1_10[0]": "0",     # personal use days
        # Income — Line 3 rents received
        "topmostSubform[0].Page1[0].Table_Income[0].Line3[0].f1_16[0]": "18000",
        # Expenses
        "topmostSubform[0].Page1[0].Table_Expenses[0].Line5[0].f1_22[0]":  "300",   # advertising
        "topmostSubform[0].Page1[0].Table_Expenses[0].Line6[0].f1_25[0]":  "400",   # auto/travel
        "topmostSubform[0].Page1[0].Table_Expenses[0].Line7[0].f1_28[0]":  "900",   # cleaning/maintenance
        "topmostSubform[0].Page1[0].Table_Expenses[0].Line8[0].f1_31[0]":  "750",   # insurance
        # Totals
        "topmostSubform[0].Page1[0].f1_15[0]": "18000",  # total rents
    })


if __name__ == "__main__":
    print("Filling IRS templates with fake data...\n")
    generate_1040()
    generate_w2()
    generate_schedule_c()
    generate_schedule_e()
    generate_k1()
    print("\nDone. Run:")
    print("  ../.venv/bin/python ../test_real.py f1040_filled.pdf fw2_filled.pdf f1040sc_filled.pdf f1040se_filled.pdf f1065sk1_filled.pdf")
