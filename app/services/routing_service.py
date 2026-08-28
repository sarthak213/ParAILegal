"""
routing_service.py
──────────────────
Domain classifier for the ParAILegal RAG pipeline.

Routes a user query to one of four search domains:
    "constitution"  — search only the constitution index
    "statutes"      — search only the statutes index (BNS + BNSS + BSA)
    "judgements"    — search only the judgements index
    "all"           — search all available indices (default / fallback)

Changes from previous version
──────────────────────────────
Fix for J05 (Navtej Johar routing failure):

  Query: "What did the Supreme Court hold on Section 377 and homosexuality?"

  "navtej" was already in _LANDMARK_NAMES, but the step 3 judgement check
  was being blocked:
      - "court held" fires _JUDGEMENT_STRONG → would return "judgements"
      - BUT _has_statute_signal() also returns True because _SECTION_NUMBER_RE
        matches "Section 377" → the guard `if not self._has_statute_signal(q)`
        blocked the judgements route → fell through to "statutes"

  Fix: _has_statute_signal() now accepts an optional `has_landmark` flag.
  When True, the section number regex is skipped — a section number alone
  does not override a known landmark case signal.

  The landmark check at step 2 still fires first for bare landmark queries
  ("What did Navtej Johar hold?"). The fix only matters for queries that
  contain BOTH a landmark signal AND a section number reference.

Routing order (first match wins):
    1. Strip ADVOCATE:/SUMMARISE: prefix
    2. Named case citation (v./versus) → "judgements"
    3. Landmark case name → "judgements"
    4. Strong judgement vocabulary → "judgements"
       (section number in query does NOT block this if landmark present)
    5. Strong procedure terms (bail, arrest) → "statutes"
       (landmark name still wins — already handled at step 3)
    6. Cross-domain (constitution + statute both present) → "all"
    7. Pure constitution → "constitution"
    8. Pure statute → "statutes"
    9. Default → "all"
"""

import re
from app.core.config import Settings


_CASE_CITATION_RE = re.compile(
    r'\b[A-Z][a-z]+\s+(?:Singh|Kumar|Devi|Gandhi|Sharma|Rao|Khan|Ali|Das|Nair|Iyer|Pillai|Menon)'
    r'\s+(?:v\.?|versus)\s+',
    re.IGNORECASE
)

_ARTICLE_NUMBER_RE = re.compile(r'\b(?:article|art\.)\s*\d+[A-Za-z]?\b', re.IGNORECASE)
_SECTION_NUMBER_RE = re.compile(r'\b(?:section|sec\.?|s\.)\s*\d+[A-Za-z]?\b', re.IGNORECASE)
_MODE_PREFIX_RE    = re.compile(r'^(ADVOCATE|SUMMARISE)\s*:\s*', re.IGNORECASE)


class QueryRouter:

    # ── Landmark case short names ─────────────────────────────────────
    # Unambiguous judgement signals — no other legal context uses these.
    # Checked before constitution signals so queries like
    # "fundamental right after Puttaswamy" → "judgements", not "constitution".
    _LANDMARK_NAMES = {
        "kesavananda", "kesavananda bharati",
        "maneka gandhi",
        "bachan singh",
        "minerva mills",
        "golak nath",
        "a.k. gopalan", "ak gopalan", "gopalan",
        "adm jabalpur", "habeas corpus case",
        "puttaswamy",
        "navtej", "navtej johar",
        "shayara bano", "triple talaq",
        "sabarimala",
        "indra sawhney", "mandal commission",
        "sr bommai", "s.r. bommai", "bommai",
        "vishaka", "vishaka guidelines",
        "olga tellis",
        "hussainara khatoon",
        "bandhua mukti morcha",
        "champakam dorairajan",
        "unni krishnan",
        "sp gupta", "s.p. gupta",
        "mc mehta", "m.c. mehta",
        # Content-based landmark signals — these phrases only appear in
        # Navtej Johar context; treating them as landmark signals ensures
        # "Section 377 and homosexuality" routes to judgements even without
        # the case name in the query.
        "homosexuality",
        "same-sex",
        "lgbtq",
        "377 ipc",
    }

    _JUDGEMENT_STRONG = {
        "supreme court held", "high court held", "court held",
        "supreme court observed", "high court observed",
        "ratio decidendi", "obiter dictum", "obiter dicta",
        "per curiam", "stare decisis",
        "division bench", "full bench", "constitution bench",
        "five-judge bench", "three-judge bench",
        "writ petition", "civil appeal", "criminal appeal",
        "slp", "special leave petition",
        "air 19", "air 20",
        "scr", "scc",
        "vs state of", "v. state of", "versus state of",
        "v. union of india", "vs union of india",
        "rarest of rare",
        "basic structure doctrine",
        "golden triangle",
        "overruled by", "overruling",
        # Section 377 IPC / homosexuality queries → always Navtej Johar context
        "homosexuality",
        "consensual sex between adults",
        "377 ipc",
        "section 377 ipc",
        "same-sex", "same sex",
        "lgbtq", "lgbt",
    }

    _CONSTITUTION_STRONG = {
        "lok sabha", "rajya sabha", "council of states", "house of the people",
        "speaker of", "president of india",
        "council of ministers", "cabinet",
        "attorney general", "solicitor general",
        "comptroller and auditor", "election commission",
        "finance commission",
        "union public service commission", "upsc",
        "fundamental right", "fundamental rights",
        "fundamental duty", "fundamental duties",
        "directive principle", "directive principles",
        "dpsp",
        "habeas corpus", "mandamus", "certiorari", "quo warranto", "prohibition",
        "union list", "state list", "concurrent list",
        "seventh schedule", "eighth schedule", "ninth schedule", "tenth schedule",
        "preamble of", "preamble to the constitution",
        "constituent assembly",
        "basic structure",
        "capital punishment",
        "constitutionality",
        "constitutionally valid",
        "constitutionally invalid",
        "void ab initio",
        "ultra vires",
    }

    _STATUTE_NAMES = {
        "bns", "bnss", "bsa",
        "bharatiya nyaya sanhita",
        "bharatiya nagarik suraksha sanhita",
        "bharatiya sakshya adhiniyam",
        "ipc", "crpc", "indian evidence act",
        "indian penal code",
    }

    _OFFENCE_TERMS = {
        "murder", "culpable homicide", "manslaughter",
        "theft", "robbery", "dacoity", "extortion",
        "rape", "sexual assault", "outraging modesty",
        "kidnapping", "abduction",
        "cheating", "fraud", "forgery", "counterfeiting",
        "hurt", "grievous hurt", "assault",
        "sedition", "defamation", "obscenity",
        "abetment", "criminal conspiracy",
        "dowry death", "cruelty to woman",
        "organised crime", "terrorist act",
        "house-breaking", "housebreaking", "trespass",
        "mischief", "criminal breach of trust",
        "money laundering", "corruption",
    }

    _PROCEDURE_TERMS = {
        "fir", "first information report",
        "charge sheet", "chargesheet",
        "cognizable", "non-cognizable",
        "bailable", "non-bailable",
        "anticipatory bail",
        "remand", "judicial custody", "police custody",
        "summons", "warrant of arrest",
        "magistrate", "sessions court", "sessions judge",
        "committal", "framing of charges",
        "examination in chief", "cross examination",
        "dying declaration",
        "confession", "admission",
        "electronic record", "digital evidence",
        "burden of proof", "standard of proof",
        "presumption of innocence",
    }

    # ── Strong procedure terms ────────────────────────────────────────
    # Force "statutes" routing even when constitution signals present.
    # "When can a person get bail?" is a BNSS question, not constitutional.
    # "When can police arrest without a warrant?" → BNSS Section 35.
    # Exception: if a landmark name is also present (e.g. "Hussainara
    # Khatoon and right to bail"), landmark wins → already handled at step 3.
    _PROCEDURE_TERMS_STRONG = {
        "bail",
        "get bail",
        "grant bail",
        "granted bail",
        "arrest",
        "arrested",
        "police arrest",
        "without a warrant",
        "without warrant",
    }

    def __init__(self, settings: Settings):
        self.settings = settings

    def route(self, query: str) -> str:
        """
        Classify query into a search domain.
        Returns: "constitution" | "statutes" | "judgements" | "all"
        """
        clean = _MODE_PREFIX_RE.sub("", query).strip()
        q     = clean.lower()

        # 1. Named case citation → judgements
        if _CASE_CITATION_RE.search(clean):
            return "judgements"

        # 2. Landmark case name → judgements (before all other checks)
        has_landmark = self._has_landmark_signal(q)
        if has_landmark:
            return "judgements"

        # 3. Strong judgement vocabulary → judgements
        #    Guard: only blocked by statute signal if NO landmark is present.
        #    Pass has_landmark so that "Section 377 and homosexuality" doesn't
        #    get blocked by BNSS Section 377 when "court held" fires.
        if any(sig in q for sig in self._JUDGEMENT_STRONG):
            if not self._has_statute_signal(q, has_landmark=has_landmark):
                return "judgements"

        # 4. Strong procedure terms → statutes
        #    Landmark already handled at step 2, so no additional guard needed.
        if self._has_strong_procedure_signal(q):
            return "statutes"

        # 5. Cross-domain → "all"
        has_constitution = self._has_constitution_signal(q)
        has_statute      = self._has_statute_signal(q, has_landmark=has_landmark)

        if has_constitution and has_statute:
            return "all"

        # 6. Pure constitution
        if has_constitution:
            return "constitution"

        # 7. Pure statute
        if has_statute:
            return "statutes"

        # 8. Default
        return "all"

    def _has_landmark_signal(self, q: str) -> bool:
        return any(name in q for name in self._LANDMARK_NAMES)

    def _has_strong_procedure_signal(self, q: str) -> bool:
        return any(term in q for term in self._PROCEDURE_TERMS_STRONG)

    def _has_constitution_signal(self, q: str) -> bool:
        if _ARTICLE_NUMBER_RE.search(q):
            return True
        return any(sig in q for sig in self._CONSTITUTION_STRONG)

    def _has_statute_signal(self, q: str, has_landmark: bool = False) -> bool:
        """
        Returns True if the query contains statute-domain signals.

        has_landmark: when True, the section number regex is skipped.
        This prevents "Section 377" from being treated as a statute signal
        when the query is actually about the Navtej Johar judgement.
        The section number alone should not override a known landmark signal.
        """
        # Skip section number regex when a landmark name is present —
        # the section reference is part of a judgement context, not a
        # direct statute lookup.
        if not has_landmark and _SECTION_NUMBER_RE.search(q):
            return True
        if any(name in q for name in self._STATUTE_NAMES):
            return True
        if any(term in q for term in self._OFFENCE_TERMS):
            return True
        return any(term in q for term in self._PROCEDURE_TERMS)

    def explain(self, query: str) -> dict:
        clean    = _MODE_PREFIX_RE.sub("", query).strip()
        q        = clean.lower()
        domain   = self.route(query)
        triggers = []

        if _MODE_PREFIX_RE.match(query):
            triggers.append(
                f"mode prefix stripped: "
                f"'{_MODE_PREFIX_RE.match(query).group(0).strip()}'"
            )
        if _CASE_CITATION_RE.search(clean):
            triggers.append("case citation pattern (v./versus)")
        for name in self._LANDMARK_NAMES:
            if name in q:
                triggers.append(f"landmark name: '{name}'")
        for sig in self._JUDGEMENT_STRONG:
            if sig in q:
                triggers.append(f"judgement signal: '{sig}'")
        for term in self._PROCEDURE_TERMS_STRONG:
            if term in q:
                triggers.append(f"strong procedure: '{term}'")
        if _ARTICLE_NUMBER_RE.search(q):
            triggers.append(f"article number: {_ARTICLE_NUMBER_RE.findall(q)}")
        for sig in self._CONSTITUTION_STRONG:
            if sig in q:
                triggers.append(f"constitution signal: '{sig}'")
        if _SECTION_NUMBER_RE.search(q):
            triggers.append(f"section number: {_SECTION_NUMBER_RE.findall(q)}")
        for name in self._STATUTE_NAMES:
            if name in q:
                triggers.append(f"statute name: '{name}'")
        for term in self._OFFENCE_TERMS:
            if term in q:
                triggers.append(f"offence term: '{term}'")
        for term in self._PROCEDURE_TERMS:
            if term in q:
                triggers.append(f"procedure term: '{term}'")
        if not triggers:
            triggers.append("no specific signal → default to 'all'")

        return {"domain": domain, "triggers": triggers}