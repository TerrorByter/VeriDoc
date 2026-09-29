"""
gldb/form_maps.py
-----------------
Semantic-to-PDF-field-name mappings for GLDB AcroForm PDFs.

Rules (steering/tech.md):
- AcroForms are read deterministically with pypdf get_fields(). Never LLM.
- Checkbox on-value is always "/Yes".
- fill_form() must not modify any page content stream.
- Templates in data/templates/ are read-only.

CLI:
    python -m gldb.form_maps debug
        Writes data/templates/debug_entity.pdf and debug_individual.pdf with
        every text field set to its semantic key and every checkbox ticked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pypdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject, NumberObject

# ---------------------------------------------------------------------------
# Address sub-maps (shared)
# ---------------------------------------------------------------------------

ADDR_REG: dict[str, Any] = {
    "block":       "Block",
    "street":      ["Street", "Street_2", "Street_3"],
    "floor":       "Floor",
    "unit":        "Unit",
    "building":    ["Building", "Building_2"],
    "city_state":  "City  State",   # double space is intentional
    "postal_code": "Postal Code",
    "country":     "Country",
}

MAIL_EXPL = (
    "If the country of the mailing address does not match the country of the "
    "registered address please explainRow1"
)

EXPL = "Explanation for Reason BCountry {n} indicated above because"   # n = 1, 2, 3

# ---------------------------------------------------------------------------
# Entity self-certification field map
# ---------------------------------------------------------------------------

ENTITY_FIELD_MAP: dict[str, Any] = {
    "legal_name":               "Full Legal Name of Entity Branch",
    "country_of_incorporation": "Country of Incorporation",
    "registered_address":       ADDR_REG,
    "mailing_different":        "Check Box1",
    "mailing_address": {
        "block":       "Block_2",
        "street":      ["Street_4", "Street_5", "Street_6"],
        "floor":       "Floor_2",
        "unit":        "Unit_2",
        "building":    ["Building_3", "Building_4"],
        "city_state":  "City  State_2",
        "postal_code": "Postal Code_2",
        "country":     "Country_2",
    },
    "mailing_explanation": MAIL_EXPL,
    "branch":        {"No": "Check Box2", "Yes": "Check Box3"},
    "hq_jurisdiction": "No YesIf yes in which jurisdiction is the headquarters located",
    "us_person":     {"No": "Check Box4", "Yes": "Check Box5"},
    "us_tin":        "Text6",
    "tax_rows": [
        {
            "country":     "Country Jurisdiction of Tax Residence1",
            "tin":         "Tax Identification Number TIN1",
            "A":           "Check Box7",
            "B+":          "Check Box8",
            "C":           "Check Box9",
            "explanation": EXPL.format(n=1),
        },
        {
            "country":     "Country Jurisdiction of Tax Residence2",
            "tin":         "Tax Identification Number TIN2",
            "A":           "Check Box10",
            "B+":          "Check Box11",
            "C":           "Check Box12",
            "explanation": EXPL.format(n=2),
        },
        {
            "country":     "Country Jurisdiction of Tax Residence3",
            "tin":         "Tax Identification Number TIN3",
            "A":           "Check Box13",
            "B+":          "Check Box14",
            "C":           "Check Box15",
            "explanation": EXPL.format(n=3),
        },
    ],
    "entity_type": {
        "A": "Check Box16", "B": "Check Box17", "C": "Check Box18",
        "D": "Check Box24", "E": "Check Box25", "F": "Check Box26",
        "G": "Check Box27", "H": "Check Box28", "I": "Check Box29",
        "J": "Check Box30", "K": "Check Box31", "L": "Check Box32",
        "M": "Check Box33",
    },
    "giin_parts": ["Text21", "Text22", "Text23"],
    "controlling_persons": [
        {
            "name": "Text47",
            "types": {
                "legal_ownership":              "Check Box34",
                "legal_senior_managing_official": "Check Box36",
                "legal_other_means":            "Check Box37",
                "trust_settlor":                "Check Box38",
                "trust_trustee":                "Check Box39",
                "trust_protector":              "Check Box40",
                "trust_beneficiary":            "Check Box41",
                "trust_other":                  "Check Box42",
                "arrangement_settlor_eq":       "Check Box35",
                "arrangement_trustee_eq":       "Check Box43",
                "arrangement_protector_eq":     "Check Box44",
                "arrangement_beneficiary_eq":   "Check Box45",
                "arrangement_other_eq":         "Check Box46",
            },
        },
        {
            "name": "Text48",
            "types": {
                "legal_ownership":              "Check Box49",
                "legal_senior_managing_official": "Check Box50",
                "legal_other_means":            "Check Box51",
                "trust_settlor":                "Check Box52",
                "trust_trustee":                "Check Box53",
                "trust_protector":              "Check Box54",
                "trust_beneficiary":            "Check Box55",
                "trust_other":                  "Check Box56",
                "arrangement_settlor_eq":       "Check Box57",
                "arrangement_trustee_eq":       "Check Box58",
                "arrangement_protector_eq":     "Check Box59",
                "arrangement_beneficiary_eq":   "Check Box60",
                "arrangement_other_eq":         "Check Box61",
            },
        },
        {
            "name": "Text75",
            "types": {
                "legal_ownership":              "Check Box62",
                "legal_senior_managing_official": "Check Box63",
                "legal_other_means":            "Check Box64",
                "trust_settlor":                "Check Box65",
                "trust_trustee":                "Check Box66",
                "trust_protector":              "Check Box67",
                "trust_beneficiary":            "Check Box68",
                "trust_other":                  "Check Box69",
                "arrangement_settlor_eq":       "Check Box70",
                "arrangement_trustee_eq":       "Check Box71",
                "arrangement_protector_eq":     "Check Box72",
                "arrangement_beneficiary_eq":   "Check Box73",
                "arrangement_other_eq":         "Check Box74",
            },
        },
    ],
    "signatories": [
        {
            "name":        "Name",
            "designation": "Designation",
            "date":        "Date DDMMYY",
            "signature":   "Signature76",
        },
        {
            "name":        "Name_2",
            "designation": "Designation_2",
            "date":        "Date DDMMYY_2",
            "signature":   "Signature77",
        },
    ],
}

# ---------------------------------------------------------------------------
# Individual self-certification field map
# ---------------------------------------------------------------------------

INDIVIDUAL_FIELD_MAP: dict[str, Any] = {
    "full_name":          "Full Legal Name as per NRICPassport",
    "date_of_birth":      "Date of Birth DDMMYYYY",
    "place_of_birth":     "Place of Birth",
    "residential_address": ADDR_REG,
    "mailing_different":  "Check Box78",
    "mailing_address": {
        "block":       "Block_2",
        "street":      ["Street_4", "Street_5", "Street_6"],
        "floor":       "Floor_2",
        "unit":        "Unit_2",
        "building":    ["Building_3"],
        "city_state":  "City  State_2",
        "postal_code": "Postal Code_2",
        "country":     "Country_2",
    },
    "mailing_explanation":         MAIL_EXPL,
    "us_status": {
        "not_us_person":      "Check Box79",
        "us_person":          "Check Box80",
        "no_longer_us_person": "Check Box81",
    },
    "not_us_born_in_us_explanation": "Text82",
    "us_tin_parts":              ["Text83", "Text84", "Text85"],
    "no_longer_us_explanation":  "Text86",
    "tax_rows": [
        {
            "country":     "Text87",
            "tin":         "Text90",
            "A":           "Check Box93",
            "B+":          "Check Box96",
            "C":           "Check Box99",
            "explanation": EXPL.format(n=1),
        },
        {
            "country":     "Text88",
            "tin":         "Text91",
            "A":           "Check Box94",
            "B+":          "Check Box97",
            "C":           "Check Box100",
            "explanation": EXPL.format(n=2),
        },
        {
            "country":     "Text89",
            "tin":         "Text92",
            "A":           "Check Box95",
            "B+":          "Check Box98",
            "C":           "Check Box101",
            "explanation": EXPL.format(n=3),
        },
    ],
    "not_resident_where_residing": "Check Box102",
    "declaration": {
        "name":      "Name as per NRIC Passport",
        "id_number": "NRIC Passport No",
        "date":      "Date DDMMYY",
        "signature": "Signature103",
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_field_names(obj: Any, out: set[str]) -> None:
    """Recursively collect all leaf string values from a field map."""
    if isinstance(obj, str):
        out.add(obj)
    elif isinstance(obj, list):
        for item in obj:
            _collect_field_names(item, out)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_field_names(v, out)


def all_pdf_field_names(field_map: dict[str, Any]) -> set[str]:
    """Return the set of every PDF field name referenced in *field_map*."""
    names: set[str] = set()
    _collect_field_names(field_map, names)
    return names


def _set_need_appearances_writer(writer: PdfWriter) -> None:
    """Set /NeedAppearances on the AcroForm so viewers render field values."""
    if "/AcroForm" not in writer._root_object:
        return
    acroform = writer._root_object["/AcroForm"].get_object()
    acroform.update({NameObject("/NeedAppearances"): BooleanObject(True)})


def fill_form(
    template_path: str | Path,
    out_path: str | Path,
    values: dict[str, str],
    mod_date: str,
) -> None:
    """Fill *template_path* AcroForm with *values* and write to *out_path*.

    - Uses PdfWriter(clone_from=template) to preserve all form structure.
    - Sets /NeedAppearances so viewers render field appearances.
    - Preserves the template's /Producer and /CreationDate.
    - Sets /ModDate to *mod_date*.
    - Does NOT modify any page content stream.
    - Checkbox values must be "/Yes" (already enforced by callers via ENTITY_FIELD_MAP).
    """
    template_path = Path(template_path)
    out_path = Path(out_path)

    # Read original metadata before cloning (clone_from may not preserve them)
    reader = PdfReader(str(template_path))
    orig_meta = reader.metadata or {}
    orig_producer = str(orig_meta.get("/Producer", ""))
    orig_creation = str(orig_meta.get("/CreationDate", ""))

    writer = PdfWriter(clone_from=str(template_path))
    _set_need_appearances_writer(writer)

    # Fill fields page-by-page
    for page in writer.pages:
        writer.update_page_form_field_values(
            page,
            values,
            auto_regenerate=False,
        )

    # Stamp metadata (preserve original Producer and CreationDate)
    new_meta: dict[str, str] = {}
    if orig_producer:
        new_meta["/Producer"] = orig_producer
    if orig_creation:
        new_meta["/CreationDate"] = orig_creation
    new_meta["/ModDate"] = mod_date
    writer.add_metadata(new_meta)

    import io as _io
    buf = _io.BytesIO()
    writer.write(buf)
    out_path.write_bytes(buf.getvalue())


# ---------------------------------------------------------------------------
# CLI — debug mode
# ---------------------------------------------------------------------------

def _debug_fill(field_map: dict[str, Any], template_path: Path, out_path: Path) -> None:
    """Fill every text field with its semantic key; tick every checkbox."""
    values: dict[str, str] = {}

    def _walk(obj: Any, semantic_key: str = "") -> None:
        if isinstance(obj, str):
            # obj is a PDF field name; use it as both key and value for text,
            # or "/Yes" for checkboxes (field names starting with "Check Box")
            if obj.startswith("Check Box") or obj.startswith("Signature"):
                values[obj] = "/Yes"
            else:
                # Use the last segment of semantic_key as readable label
                values[obj] = semantic_key or obj
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _walk(item, f"{semantic_key}[{i}]")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                _walk(v, k)

    _walk(field_map)
    fill_form(
        template_path,
        out_path,
        values,
        mod_date="D:20260101100000+08'00'",
    )
    print(f"  Debug form written: {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "debug":
        print("Usage: python -m gldb.form_maps debug")
        sys.exit(1)

    templates = Path("data/templates")
    entity_tpl   = templates / "gldb_fatca_crs_entity_2025-11.pdf"
    individual_tpl = templates / "gldb_fatca_crs_individual_2025-11.pdf"

    for tpl in (entity_tpl, individual_tpl):
        if not tpl.exists():
            raise FileNotFoundError(
                f"Template not found: {tpl}\n"
                "Please copy GLDB's official blank forms to data/templates/."
            )

    _debug_fill(ENTITY_FIELD_MAP,      entity_tpl,     templates / "debug_entity.pdf")
    _debug_fill(INDIVIDUAL_FIELD_MAP,  individual_tpl, templates / "debug_individual.pdf")
    print("Debug forms written to data/templates/.")
