"""
Entity Extractor — ICD-10 codes and medications from scrubbed clinical notes.

Two-pass extraction:
  Pass 1 — spaCy NER: medication names, dosages, frequency terms.
  Pass 2 — LLM (OpenAI or mock): ICD-10 codes + descriptions from
            the clinical assessment / impression sections.

IMPORTANT: Only scrubbed text is ever passed to the LLM.
"""

import json
import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# spaCy
try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    logger.warning("spaCy not installed; NER pass will be regex-only.")

# OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

MOCK_MODE: bool = os.getenv("MOCK_MODE", "true").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ICDCode:
    code: str
    description: str
    confidence: float
    confidence_label: str = ""
    source: str = "llm"

    def __post_init__(self) -> None:
        self.confidence_label = _confidence_label(self.confidence)


@dataclass
class Medication:
    name: str
    dose: str
    frequency: str
    route: str
    confidence: float
    confidence_label: str = ""
    source: str = "spacy"

    def __post_init__(self) -> None:
        self.confidence_label = _confidence_label(self.confidence)


@dataclass
class ExtractionResult:
    icd_codes: list[ICDCode]
    medications: list[Medication]
    raw_llm_response: str
    extraction_mode: str   # "mock" | "openai"
    low_confidence_count: int = 0

    def __post_init__(self) -> None:
        self.low_confidence_count = sum(
            1 for e in [*self.icd_codes, *self.medications] if e.confidence < 0.7
        )

    def to_dict(self) -> dict:
        return {
            "icd_codes": [vars(c) for c in self.icd_codes],
            "medications": [vars(m) for m in self.medications],
            "raw_llm_response": self.raw_llm_response,
            "extraction_mode": self.extraction_mode,
            "low_confidence_count": self.low_confidence_count,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _confidence_label(score: float) -> str:
    if score >= 0.85:
        return "HIGH"
    if score >= 0.60:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Drug / dosage patterns (regex)
# ---------------------------------------------------------------------------

DOSE_PATTERN = re.compile(
    r'(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|mL|units?|IU)',
    re.IGNORECASE,
)

FREQ_MAP: dict[str, str] = {
    "QD": "once daily", "QHS": "at bedtime", "BID": "twice daily",
    "TID": "three times daily", "QID": "four times daily", "PRN": "as needed",
    "Q4H": "every 4 hours", "Q6H": "every 6 hours", "Q8H": "every 8 hours",
    "Q12H": "every 12 hours", "STAT": "immediately", "QAM": "every morning",
    "QPM": "every evening", "Q2W": "every 2 weeks", "QWK": "weekly",
}

ROUTE_MAP: dict[str, str] = {
    "PO": "oral", "IV": "intravenous", "IM": "intramuscular",
    "SC": "subcutaneous", "SQ": "subcutaneous", "MDI": "inhaled",
    "PR": "rectal", "SL": "sublingual", "TOP": "topical",
}

# Common medications in clinical notes (lower-case for matching)
KNOWN_DRUGS: list[str] = [
    "aspirin", "metoprolol", "atorvastatin", "lisinopril", "metformin",
    "empagliflozin", "amlodipine", "hydrochlorothiazide", "losartan",
    "azithromycin", "guaifenesin", "albuterol", "sertraline", "gabapentin",
    "cyclobenzaprine", "naproxen", "rosuvastatin", "fenofibrate", "adalimumab",
    "methotrexate", "folic acid", "prednisone", "methylprednisolone",
    "cefazolin", "ketorolac", "ondansetron", "ipratropium",
    "fluticasone", "salmeterol", "fluticasone/salmeterol",
]

# Build a regex that matches any known drug (word boundary aware)
DRUG_REGEX = re.compile(
    r'\b(' + '|'.join(re.escape(d) for d in KNOWN_DRUGS) + r')\b',
    re.IGNORECASE,
)


def _extract_medications_regex(text: str) -> list[Medication]:
    """Regex-based medication extraction (fallback / supplement to spaCy)."""
    medications: list[Medication] = []
    seen: set[str] = set()

    for match in DRUG_REGEX.finditer(text):
        name = match.group().strip()
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)

        # Look 60 chars ahead for dose + frequency
        context = text[match.start(): match.start() + 80]
        dose_match = DOSE_PATTERN.search(context)
        dose = f"{dose_match.group(1)}{dose_match.group(2)}" if dose_match else "unknown"

        freq = "unknown"
        for abbr, full in FREQ_MAP.items():
            if abbr.lower() in context.lower():
                freq = full
                break

        route = "oral"
        for abbr, full in ROUTE_MAP.items():
            if re.search(r'\b' + abbr + r'\b', context, re.IGNORECASE):
                route = full
                break

        # Higher confidence if dose found
        confidence = 0.88 if dose != "unknown" else 0.62
        medications.append(Medication(
            name=name,
            dose=dose,
            frequency=freq,
            route=route,
            confidence=confidence,
            source="regex",
        ))

    return medications


def _extract_medications_spacy(text: str, nlp) -> list[Medication]:  # type: ignore[valid-type]
    """spaCy-based medication extraction."""
    doc = nlp(text)
    regex_results = _extract_medications_regex(text)
    seen_names = {m.name.lower() for m in regex_results}

    spacy_extras: list[Medication] = []
    for ent in doc.ents:
        if ent.label_ in ("DRUG", "CHEMICAL") and ent.text.lower() not in seen_names:
            seen_names.add(ent.text.lower())
            context = text[max(0, ent.start_char - 10): ent.end_char + 60]
            dose_match = DOSE_PATTERN.search(context)
            dose = (
                f"{dose_match.group(1)}{dose_match.group(2)}" if dose_match else "unknown"
            )
            spacy_extras.append(Medication(
                name=ent.text,
                dose=dose,
                frequency="unknown",
                route="unknown",
                confidence=0.78,
                source="spacy",
            ))

    return regex_results + spacy_extras


# ---------------------------------------------------------------------------
# Mock LLM responses
# ---------------------------------------------------------------------------

MOCK_ICD_RESPONSES: dict[str, list[dict]] = {
    "default": [
        {"code": "Z00.00", "description": "Encounter for general adult medical examination", "confidence": 0.80},
    ],
    "chest pain": [
        {"code": "I21.9",  "description": "Acute myocardial infarction, unspecified", "confidence": 0.92},
        {"code": "R07.9",  "description": "Chest pain, unspecified", "confidence": 0.75},
    ],
    "diabetes": [
        {"code": "E11.65", "description": "Type 2 diabetes mellitus with hyperglycemia", "confidence": 0.91},
        {"code": "E11.9",  "description": "Type 2 diabetes mellitus without complications", "confidence": 0.68},
    ],
    "hypertension": [
        {"code": "I10",    "description": "Essential (primary) hypertension", "confidence": 0.95},
    ],
    "pneumonia": [
        {"code": "J18.9",  "description": "Pneumonia, unspecified organism", "confidence": 0.89},
        {"code": "J06.9",  "description": "Acute upper respiratory infection, unspecified", "confidence": 0.55},
    ],
    "depression": [
        {"code": "F32.2",  "description": "Major depressive disorder, single episode, severe", "confidence": 0.93},
        {"code": "F41.1",  "description": "Generalized anxiety disorder", "confidence": 0.58},
    ],
    "back pain": [
        {"code": "M51.16", "description": "Intervertebral disc degeneration, lumbar region", "confidence": 0.87},
        {"code": "M54.5",  "description": "Low back pain", "confidence": 0.72},
    ],
    "hyperlipidemia": [
        {"code": "E78.2",  "description": "Mixed hyperlipidemia", "confidence": 0.90},
        {"code": "E66.09", "description": "Other obesity due to excess calories", "confidence": 0.82},
    ],
    "asthma": [
        {"code": "J45.41", "description": "Moderate persistent asthma with (acute) exacerbation", "confidence": 0.91},
    ],
    "appendicitis": [
        {"code": "K37",    "description": "Unspecified appendicitis", "confidence": 0.88},
        {"code": "Z96.89", "description": "Presence of other specified functional implants", "confidence": 0.60},
    ],
    "rheumatoid": [
        {"code": "M05.79", "description": "Rheumatoid arthritis with rheumatoid factor of multiple sites", "confidence": 0.90},
    ],
}

ICD_LOOKUP: dict[str, str] = {
    # Cardiovascular
    "I21.9": "Acute myocardial infarction, unspecified",
    "I10":   "Essential (primary) hypertension",
    "I25.10": "Atherosclerotic heart disease of native coronary artery",
    # Endocrine
    "E11.65": "Type 2 diabetes mellitus with hyperglycemia",
    "E11.9":  "Type 2 diabetes mellitus without complications",
    "E78.2":  "Mixed hyperlipidemia",
    "E66.09": "Other obesity due to excess calories",
    # Respiratory
    "J18.9":  "Pneumonia, unspecified organism",
    "J45.41": "Moderate persistent asthma with (acute) exacerbation",
    "J06.9":  "Acute upper respiratory infection, unspecified",
    # Mental health
    "F32.2":  "Major depressive disorder, single episode, severe",
    "F41.1":  "Generalized anxiety disorder",
    # Musculoskeletal
    "M51.16": "Intervertebral disc degeneration, lumbar region",
    "M54.5":  "Low back pain",
    "M05.79": "Rheumatoid arthritis with rheumatoid factor",
    # GI / Surgery
    "K37":    "Unspecified appendicitis",
    # Other
    "N18.2":  "Chronic kidney disease, stage 2",
    "Z00.00": "Encounter for general adult medical examination",
    "Z96.89": "Presence of other specified functional implants",
}

# Inline ICD code pattern: "ICD-10: I21.9" or "(I21.9)"
INLINE_ICD_RE = re.compile(
    r'\(?\s*(?:ICD[-\s]?10[:.\s]*)?([A-Z]\d{2}(?:\.\d{1,2})?)\s*\)?',
)


def _mock_llm_extract(scrubbed_text: str) -> tuple[list[ICDCode], str]:
    """
    Deterministic mock LLM extraction.
    1. Detect inline ICD codes in the scrubbed text.
    2. Supplement with keyword-matched canned responses.
    """
    icd_codes: list[ICDCode] = []
    seen_codes: set[str] = set()

    # Inline codes first (highest confidence — they were explicit in the note)
    for match in INLINE_ICD_RE.finditer(scrubbed_text):
        code = match.group(1).upper()
        if code not in seen_codes and code in ICD_LOOKUP:
            seen_codes.add(code)
            icd_codes.append(ICDCode(
                code=code,
                description=ICD_LOOKUP[code],
                confidence=0.92,
                source="inline",
            ))

    # Keyword fallback
    text_lower = scrubbed_text.lower()
    for keyword, entries in MOCK_ICD_RESPONSES.items():
        if keyword == "default":
            continue
        if keyword in text_lower:
            for entry in entries:
                if entry["code"] not in seen_codes:
                    seen_codes.add(entry["code"])
                    icd_codes.append(ICDCode(**entry))

    if not icd_codes:
        for entry in MOCK_ICD_RESPONSES["default"]:
            icd_codes.append(ICDCode(**entry))

    mock_response = json.dumps(
        [{"code": c.code, "description": c.description, "confidence": c.confidence}
         for c in icd_codes],
        indent=2,
    )
    return icd_codes, mock_response


def _openai_extract(scrubbed_text: str, client: "OpenAI") -> tuple[list[ICDCode], str]:
    """Real OpenAI extraction (used when MOCK_MODE=false and API key is set)."""
    prompt = (
        "You are a clinical coding assistant. Extract ICD-10 codes from the following "
        "de-identified clinical note. Return a JSON array where each element has: "
        "code (string), description (string), confidence (float 0-1).\n\n"
        f"Clinical Note:\n{scrubbed_text}\n\n"
        "Respond with ONLY valid JSON."
    )
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=512,
    )
    raw = response.choices[0].message.content or "[]"
    items = json.loads(raw)
    codes = [ICDCode(**item) for item in items if isinstance(item, dict)]
    return codes, raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Module-level spaCy model (lazy)
_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None and SPACY_AVAILABLE:
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.warning("en_core_web_sm not found; falling back to regex extraction.")
    return _nlp


def extract_entities(scrubbed_text: str) -> ExtractionResult:
    """
    Extract ICD-10 codes and medications from already-scrubbed text.

    NEVER call this with raw (un-scrubbed) text.
    """
    # --- Pass 1: Medication extraction (spaCy + regex) ---
    nlp = _get_nlp()
    if nlp is not None:
        medications = _extract_medications_spacy(scrubbed_text, nlp)
    else:
        medications = _extract_medications_regex(scrubbed_text)

    # --- Pass 2: ICD-10 extraction (LLM or mock) ---
    if MOCK_MODE or not OPENAI_AVAILABLE:
        icd_codes, raw_response = _mock_llm_extract(scrubbed_text)
        mode = "mock"
    else:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set; falling back to mock extraction.")
            icd_codes, raw_response = _mock_llm_extract(scrubbed_text)
            mode = "mock"
        else:
            client = OpenAI(api_key=api_key)
            icd_codes, raw_response = _openai_extract(scrubbed_text, client)
            mode = "openai"

    return ExtractionResult(
        icd_codes=icd_codes,
        medications=medications,
        raw_llm_response=raw_response,
        extraction_mode=mode,
    )
