"""
dataset_loader.py
─────────────────
Three loaders — one file, one pattern.

    ConstitutionLoader  — loads constitution_final.jsonl
    StatuteLoader       — loads bns_clean.jsonl, bnss_clean.jsonl, bsa_clean.jsonl
    JudgementLoader     — loads landmarks.jsonl (and any future judgement files)

All three return the same tuple:
    (texts: List[str], metadata: List[Dict])

    texts[i]    — string passed to the embedding model.
                  Format: "<citation_field>\n<full_text>"
                  Prepending the citation means case names, article numbers,
                  and rhetorical role labels are searchable via vector similarity.

    metadata[i] — full normalised chunk dict, parallel to texts[i].
                  Returned in search results and passed to the LLM context.

Fix applied (vs original)
──────────────────────────
The constitution JSONL (wikisource_2020 source) does NOT populate the
`citation` field. The original _embed_text() therefore produced embed
strings like "\n<raw article text>" — no identifying prefix at all.

This caused retrieval scores of ~0.03 because the BGE embedding for
"fundamental rights Part III Constitution" had no label in the chunk
vectors for Articles 12-35 to anchor to.

Statutes (BNS/BNSS/BSA) already have `citation` fully populated —
e.g. "Section 103 (1) — Punishment for murder\nChapter VI, BNS 2023"
— so _embed_text() works correctly for them and is unchanged.

The fix: a dedicated _embed_text_constitution() that synthesizes a rich
prefix from article_number + title + part_name, which ARE present in
every constitution chunk. The ConstitutionLoader also now synthesizes
`citation` and `hierarchy` metadata so the API response and LLM context
always contain a human-readable citation.

After replacing this file, delete the old constitution index and rebuild:

    1. Delete data/index/constitution/index.faiss
       Delete data/index/constitution/store.pkl

    2. Restart the server — it will auto-rebuild the constitution index
       on startup (force_rebuild=False checks for missing files).

Or force-rebuild all indices:

    python -c "
    import asyncio
    from app.core.config import settings
    from app.services.rag_services import RAGSystem
    async def rebuild():
        rag = RAGSystem(settings)
        await rag.initialize(force_rebuild=True)
    asyncio.run(rebuild())
    "
"""

import json
import os
from typing import List, Dict, Tuple

from app.core.config import Settings


# ══════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════

def _load_jsonl(file_path: str) -> List[Dict]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Dataset file not found: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _embed_text(chunk: Dict) -> str:
    """
    Statute embed text — citation field is already populated in BNS/BNSS/BSA.
    e.g. "Section 103 (1) — Punishment for murder\nChapter VI, BNS 2023"
    Prepending the citation makes section numbers and titles searchable.
    """
    citation = chunk.get("citation", "").strip()
    body     = chunk.get("full_text") or chunk.get("text", "")
    if citation and body:
        return f"{citation}\n{body}"
    return body or citation


def _embed_text_constitution(chunk: Dict) -> str:
    """
    Constitution embed text.

    The constitution JSONL (wikisource_2020) does NOT have a citation field.
    We synthesize a rich prefix from the fields that ARE present:
        article_number, title, part_name

    Result format:
        "Article 21 — Protection of life and personal liberty
         Part III: THE RIGHT TO LIFE AND PERSONAL LIBERTY
         21. No person shall be deprived of his life or personal liberty..."

    This gives BGE enough context to correctly place Articles 12-35 in the
    "fundamental rights" neighbourhood of the vector space, which was
    completely missing before this fix.

    Special cases:
        PREAMBLE        -> "Preamble — Constitution of India\n<text>"
        SCHEDULE_N      -> "Third Schedule — <title>\n<text>"
        bare article    -> "Article N — <title>\nPart III: <part_name>\n<text>"
    """
    art_num   = (chunk.get("article_number") or "").strip()
    title     = (chunk.get("title") or "").strip()
    part_name = (chunk.get("part_name") or "").strip()
    body      = chunk.get("full_text") or chunk.get("text", "")

    if art_num.upper() == "PREAMBLE":
        prefix = "Preamble — Constitution of India"
    elif art_num.upper().startswith("SCHEDULE"):
        readable = art_num.replace("_", " ").title()
        prefix = f"{readable} — {title}" if title else readable
    else:
        prefix = f"Article {art_num}"
        if title:
            prefix += f" — {title}"
        if part_name:
            prefix += f"\n{part_name}"

    if prefix and body:
        return f"{prefix}\n{body}"
    return body or prefix


def _embed_text_judgement(item: Dict) -> str:
    """
    Judgement embed text — richer prefix than statutes.

    Format:
        "<case_name> <citation>
         <court> (<year>)
         <rhetorical_role_label>
         <text>"

    The case name + citation in the prefix ensures FAISS can find
    "Maneka Gandhi Article 21" even if the ratio text doesn't repeat
    the case name. The rhetorical role label helps distinguish ratio
    from held from analysis chunks for the same case.
    """
    case_name = item.get("case_name", "").strip()
    citation  = item.get("citation", "").strip()
    court     = item.get("court", "Supreme Court of India")
    year      = item.get("year", "")
    role      = item.get("rhetorical_role", "ratio").replace("_", " ").title()
    body      = item.get("full_text") or item.get("text", "")

    header = f"{case_name} {citation}\n{court} ({year})\n{role}"
    if body:
        return f"{header}\n{body}"
    return header


# ══════════════════════════════════════════════════════════════════════
# Constitution Loader
# ══════════════════════════════════════════════════════════════════════

_CONSTITUTION_TITLE = "Constitution of India"

# Maps part_number (Roman numeral string from JSONL) to a human-readable
# part label. Used to enrich the synthesized citation for the LLM context
# so it can say "Part III — Fundamental Rights" rather than just "Part III".
_PART_LABELS: Dict[str, str] = {
    "I":     "The Union and Its Territory",
    "II":    "Citizenship",
    "III":   "Fundamental Rights",
    "IV":    "Directive Principles of State Policy",
    "IVA":   "Fundamental Duties",
    "V":     "The Union",
    "VI":    "The States",
    "VII":   "States in Part B of the First Schedule (Repealed)",
    "VIII":  "The Union Territories",
    "IX":    "The Panchayats",
    "IXA":   "The Municipalities",
    "IXB":   "The Co-operative Societies",
    "X":     "The Scheduled and Tribal Areas",
    "XI":    "Relations Between the Union and the States",
    "XII":   "Finance, Property, Contracts and Suits",
    "XIII":  "Trade, Commerce and Intercourse Within India",
    "XIV":   "Services Under the Union and the States",
    "XIVA":  "Tribunals",
    "XV":    "Elections",
    "XVI":   "Special Provisions Relating to Certain Classes",
    "XVII":  "Official Language",
    "XVIII": "Emergency Provisions",
    "XIX":   "Miscellaneous",
    "XX":    "Amendment of the Constitution",
    "XXI":   "Temporary, Transitional and Special Provisions",
    "XXII":  "Short Title, Commencement and Repeals",
}


class ConstitutionLoader:
    """Loads constitution_final.jsonl."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def load_dataset(self) -> Tuple[List[str], List[Dict]]:
        file_path = self.settings.CONSTITUTION_FILE
        print(f"Loading Constitution: {os.path.basename(file_path)}")

        raw = _load_jsonl(file_path)

        texts:    List[str]  = []
        metadata: List[Dict] = []

        for item in raw:
            # Use constitution-specific embed function that synthesizes a prefix
            text = _embed_text_constitution(item)
            if not text.strip():
                continue

            art_num  = (item.get("article_number") or "").strip()
            title    = (item.get("title") or "").strip()
            part_num = (item.get("part_number") or "").strip()
            part_name = (item.get("part_name") or "").strip()

            # Synthesize citation and hierarchy — JSONL has neither populated.
            if art_num.upper() == "PREAMBLE":
                synthesized_citation  = "Preamble — Constitution of India"
                synthesized_hierarchy = "Preamble"

            elif art_num.upper().startswith("SCHEDULE"):
                readable = art_num.replace("_", " ").title()
                synthesized_citation  = f"{readable} — {title}" if title else readable
                synthesized_hierarchy = readable

            else:
                # e.g. "Article 21 — Protection of life and personal liberty
                #        Part III: Fundamental Rights"
                synthesized_citation = f"Article {art_num}"
                if title:
                    synthesized_citation += f" — {title}"
                part_label = _PART_LABELS.get(part_num, part_name)
                if part_label:
                    synthesized_citation += f"\nPart {part_num}: {part_label}"
                synthesized_hierarchy = f"Article {art_num}"

            meta: Dict = {
                "chunk_id":         item.get("chunk_id", ""),
                "source_type":      "constitution",
                "document_title":   _CONSTITUTION_TITLE,
                "part":             part_name,
                "section":          art_num,
                "label":            item.get("label", ""),
                "hierarchy":        synthesized_hierarchy,
                "chunk_type":       item.get("chunk_type", "article"),
                "text":             item.get("text", ""),
                "full_text":        item.get("full_text") or item.get("text", ""),
                "citation":         synthesized_citation,
                "cross_references": item.get("cross_references", []),
                "token_count":      item.get("token_count", len(text.split())),
                "needs_split":      item.get("needs_split", False),
                "status":           _constitution_status(item),
            }

            texts.append(text)
            metadata.append(meta)

        print(f"  {len(texts)} chunks  [constitution]  {_CONSTITUTION_TITLE}")
        return texts, metadata


def _constitution_status(item: Dict) -> str:
    if item.get("status"):  # set by scripts/build_constitution.py
        return item["status"]
    text = item.get("text", "")
    if text.lstrip().startswith("[OMITTED]"):
        return "omitted"
    return "active"


# ══════════════════════════════════════════════════════════════════════
# Statute Loader
# ══════════════════════════════════════════════════════════════════════

_STATUTE_TITLES: Dict[str, str] = {
    "bns":  "Bharatiya Nyaya Sanhita 2023",
    "bnss": "Bharatiya Nagarik Suraksha Sanhita 2023",
    "bsa":  "Bharatiya Sakshya Adhiniyam 2023",
    "ipc":  "Indian Penal Code 1860 (repealed)",
}

_STATUTE_STATUS: Dict[str, str] = {
    "bns":  "active",
    "bnss": "active",
    "bsa":  "active",
    "ipc":  "repealed",
}


class StatuteLoader:
    """
    Loads every file listed in settings.STATUTE_FILES.

    Unchanged from original — BNS/BNSS/BSA JSONL files already have the
    citation field fully populated, so _embed_text() works correctly.

    Example embed text produced:
        "Section 103 (1) — Punishment for murder
         Chapter VI, Bharatiya Nyaya Sanhita 2023
         103.(1) Whoever commits murder shall be punished with death..."
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def load_dataset(self) -> Tuple[List[str], List[Dict]]:
        if not self.settings.STATUTE_FILES:
            print("No statute files configured in settings.STATUTE_FILES")
            return [], []

        all_texts:    List[str]  = []
        all_metadata: List[Dict] = []

        for file_path in self.settings.STATUTE_FILES:
            if not os.path.exists(file_path):
                print(f"  WARNING: statute file not found, skipping: {file_path}")
                continue
            texts, metadata = self._load_one(file_path)
            all_texts.extend(texts)
            all_metadata.extend(metadata)

        print(f"  {len(all_texts)} statute chunks total "
              f"across {len(self.settings.STATUTE_FILES)} configured file(s)")
        return all_texts, all_metadata

    def _load_one(self, file_path: str) -> Tuple[List[str], List[Dict]]:
        print(f"Loading statute: {os.path.basename(file_path)}")
        raw = _load_jsonl(file_path)

        if not raw:
            print(f"  WARNING: {file_path} is empty")
            return [], []

        source_type = raw[0].get("source_type", "unknown")
        # corpus built by scripts/build_corpus.py names the Act and its status in each record
        doc_title   = raw[0].get("act_title") or _STATUTE_TITLES.get(source_type, source_type.upper())
        status      = raw[0].get("status") or _STATUTE_STATUS.get(source_type, "active")

        texts:    List[str]  = []
        metadata: List[Dict] = []

        for item in raw:
            text = _embed_text(item)
            if not text.strip():
                continue

            meta: Dict = {
                "chunk_id":         item.get("chunk_id", ""),
                "source_type":      source_type,
                "document_title":   doc_title,
                "part":             item.get("chapter", ""),
                "section":          item.get("section_number", ""),
                "label":            item.get("label", ""),
                "hierarchy":        item.get("hierarchy", ""),
                "chunk_type":       item.get("chunk_type", "clause"),
                "text":             item.get("text", ""),
                "full_text":        item.get("full_text") or item.get("text", ""),
                "citation":         item.get("citation", ""),
                "cross_references": item.get("cross_references", []),
                "token_count":      item.get("token_count", len(text.split())),
                "needs_split":      item.get("needs_split", False),
                "status":           status,
                "ipc_equivalent":   item.get("ipc_equivalent"),
                "chapter_title":    item.get("chapter_title", ""),
                "section_title":    item.get("section_title", ""),
                "act_code":         item.get("act_code") or source_type.upper(),
                "corresponds_to":   item.get("corresponds_to", []),
                "replaced_by":      item.get("replaced_by"),
                "repeal_pending":   item.get("repeal_pending", False),  # repealed from a date not yet known
                "derived_links":    item.get("derived_links", []),  # [{ref, score}]: derived, not official
                "authority":        item.get("authority"),
                "scope":            item.get("scope", "national"),  # or "regional" (a local Act)
                "places":           item.get("places", []),          # the territory a regional Act covers
                "text_quality":     item.get("text_quality", "clean"),
                "year":             item.get("year"),
            }

            texts.append(text)
            metadata.append(meta)

        print(f"  {len(texts)} chunks  [{source_type}]  {doc_title}")
        return texts, metadata


# ══════════════════════════════════════════════════════════════════════
# Judgement Loader
# ══════════════════════════════════════════════════════════════════════

_RHETORICAL_ROLE_LABELS: Dict[str, str] = {
    "ratio":     "Ratio Decidendi",
    "held":      "Held",
    "analysis":  "Analysis",
    "facts":     "Facts",
    "issues":    "Issues",
    "arguments": "Arguments",
    "order":     "Order",
}


class JudgementLoader:
    """
    Loads every file listed in settings.JUDGEMENT_FILES.

    Currently: data/landmarks.jsonl (26 hand-curated constitutional chunks).
    Future:    ILDC dataset, Indian Kanoon exports, High Court judgements.

    Adding more judgement files — append to settings.JUDGEMENT_FILES.
    No other code changes needed.

    Critical fields
    ───────────────
    overruled_by  — non-null means this ratio is no longer good law.
    rhetorical_role — "ratio" and "held" are the highest-value chunks.
    is_constitution_bench — True for 5+ judge benches.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def load_dataset(self) -> Tuple[List[str], List[Dict]]:
        judgement_files = getattr(self.settings, "JUDGEMENT_FILES", [])

        if not judgement_files:
            print("No judgement files configured in settings.JUDGEMENT_FILES")
            return [], []

        all_texts:    List[str]  = []
        all_metadata: List[Dict] = []

        for file_path in judgement_files:
            file_path = str(file_path)
            if not os.path.exists(file_path):
                print(f"  WARNING: judgement file not found, skipping: {file_path}")
                continue
            texts, metadata = self._load_one(file_path)
            all_texts.extend(texts)
            all_metadata.extend(metadata)

        print(f"  {len(all_texts)} judgement chunks total "
              f"across {len(judgement_files)} configured file(s)")
        return all_texts, all_metadata

    def _load_one(self, file_path: str) -> Tuple[List[str], List[Dict]]:
        print(f"Loading judgements: {os.path.basename(file_path)}")
        raw = _load_jsonl(file_path)

        if not raw:
            print(f"  WARNING: {file_path} is empty")
            return [], []

        texts:    List[str]  = []
        metadata: List[Dict] = []

        skipped_roles = 0

        for item in raw:
            role = item.get("rhetorical_role", "ratio")
            if role in ("facts", "arguments"):
                skipped_roles += 1
                continue

            text = _embed_text_judgement(item)
            if not text.strip():
                continue

            overruled_by = item.get("overruled_by")
            status = "overruled" if overruled_by else item.get("status", "active")

            role_label = _RHETORICAL_ROLE_LABELS.get(role, role.title())
            bench_str  = (
                f"{item.get('bench_strength', '')}-Judge Constitution Bench"
                if item.get("is_constitution_bench") and item.get("bench_strength")
                else item.get("court", "Supreme Court of India")
            )
            citation_display = (
                f"{item.get('case_name', '')} {item.get('citation', '')}\n"
                f"{bench_str}, {item.get('year', '')}\n"
                f"{role_label}"
            )
            if overruled_by:
                citation_display += f"\n[OVERRULED by {overruled_by}]"

            meta: Dict = {
                "chunk_id":       item.get("chunk_id", ""),
                "source_type":    "judgement",
                "document_title": item.get("case_name", ""),
                "citation":       citation_display,
                "hierarchy":      item.get("citation_display", item.get("citation", "")),
                "section":        None,
                "label":          None,
                "chunk_type":     item.get("chunk_type", role),
                "text":           item.get("text", ""),
                "full_text":      item.get("full_text") or item.get("text", ""),
                "status":         status,
                "token_count":    item.get("token_count", len(text.split())),
                "cross_references": item.get("articles_cited", []),

                "case_name":             item.get("case_name", ""),
                "case_citation":         item.get("citation", ""),
                "court":                 item.get("court", "Supreme Court of India"),
                "bench_strength":        item.get("bench_strength"),
                "year":                  item.get("year"),
                "date":                  item.get("date"),
                "rhetorical_role":       role,
                "is_constitution_bench": item.get("is_constitution_bench", False),
                "is_landmark":           item.get("is_landmark", False),
                "overruled_by":          overruled_by,
                "overrules":             item.get("overrules", []),
                "articles_cited":        item.get("articles_cited", []),
                "statutes_cited":        item.get("statutes_cited", []),
            }

            texts.append(text)
            metadata.append(meta)

        if skipped_roles:
            print(f"  Skipped {skipped_roles} facts/arguments chunks (low RAG value)")
        print(f"  {len(texts)} chunks  [judgement]  "
              f"{os.path.basename(file_path)}")
        return texts, metadata