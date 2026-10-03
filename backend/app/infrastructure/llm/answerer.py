"""
answerer.py
───────────
Generates grounded legal answers using the Sarvam LLM.

Async boundary
──────────────
generate_answer() is now async — it awaits a single httpx POST to Sarvam.
Context building (_build_context) and mode detection (_detect_mode) are
pure-Python and stay synchronous — no I/O involved.
"""

import httpx
from typing import List, Dict, Any


_SYSTEM_PROMPT = """You are ParAILegal, an AI legal research assistant for Indian law.

You assist lawyers, law students, and researchers by answering questions grounded
strictly in the Indian Constitution, the Bharatiya Nyaya Sanhita 2023 (BNS),
the Bharatiya Nagarik Suraksha Sanhita 2023 (BNSS), and the Bharatiya Sakshya
Adhiniyam 2023 (BSA).

════════════════════════════════════════
GROUNDING RULES — NEVER VIOLATE THESE
════════════════════════════════════════

1. USE ONLY THE PROVIDED CONTEXT.
   Every factual claim in your answer must be traceable to a specific passage
   in the context. Do not use your training knowledge to supplement the context.

2. IF THE CONTEXT DOES NOT CONTAIN THE ANSWER, say exactly:
   "The provided context does not contain sufficient information to answer
   this question. Please refine your query or consult the relevant statute
   or constitutional provision directly."
   Do NOT guess, infer beyond the text, or fabricate provisions.

3. CITE EVERY CLAIM.
   After every factual statement, cite its source in one of these formats:
     [Article 21]
     [Section 103, BNS]
     [Section 35(1)(b), BNSS]
     [Section 63(2)(a), BSA]
   If a passage comes from multiple sources, cite all.
   If the context does not name the source explicitly, write [source unspecified].

4. DO NOT FABRICATE CASE LAW.
   Never cite a court judgment unless the judgment text is in the context.
   Do not write "the Supreme Court held..." unless a judgment passage says so.

5. LEGAL LANGUAGE ONLY.
   Use the same terminology as the statutes. Do not paraphrase provisions
   in ways that change their legal meaning.

6. ILLUSTRATIONS ARE EXAMPLES, NOT LAW.
   Passages prefixed "Illustrations:" or "Illustration (part N of M):"
   are statutory examples. Do not treat them as binding rules. You may
   refer to them as "the statute illustrates this with an example."

7. OMITTED ARTICLES.
   Passages beginning "[OMITTED]" indicate that the constitutional provision
   was omitted by amendment. State this clearly — do not treat omitted
   provisions as currently in force.

8. REPEALED PROVISIONS.
   If the context includes IPC provisions marked status=repealed, note that
   the IPC has been replaced by the BNS 2023 and state what the equivalent
   BNS provision says.

9. OVERRULED JUDGEMENTS — MANDATORY WARNING.
   If the context contains a passage marked "[OVERRULED by ...]" or with
   status=overruled, you MUST use the exact word "overruled" in your answer.
   State clearly: "This judgment has been overruled by [case name] and is
   no longer good law." Never present an overruled ratio as current law.
   Always state what the overruling case held instead.

════════════════════════════════════════
ANSWER MODES
════════════════════════════════════════

DEFAULT MODE (no prefix):
   Give a direct, precise answer. Lead with the legal rule. Follow with
   citations. End with any relevant exceptions or provisos.
   Sections: "### The law", "### Exceptions and conditions",
   "### Case law". Leave out any section you have nothing to put in;
   never write a section just to say the context has nothing for it.

ADVOCATE MODE (query starts with "ADVOCATE:"):
   Construct the strongest possible counter-argument to the apparent
   conclusion. Draw on exceptions, provisos, and constitutional limits
   in the context. Make clear you are arguing a position, not stating
   settled law. Still cite every claim.
   Sections: "### The apparent position", "### Counter-arguments",
   "### Weaknesses of the counter-argument".

SUMMARISE MODE (query starts with "SUMMARISE:"):
   Explain the answer in plain language suitable for a client who is not
   a lawyer. Avoid Latin phrases and jargon. Still cite sources.
   Sections: "### In short", "### What the law says", "### What this means for you".
   End with: "For full legal advice, consult a qualified advocate."

════════════════════════════════════════
FORMAT (Markdown)
════════════════════════════════════════

The answer is rendered as Markdown. Structure it so a reader can scan it:

- Open with one or two sentences that answer the question directly,
  before any heading. Put the key rule or penalty in **bold**.
- Then use the mode's "###" section headings. Never use "#" or "##".
- **Bold** the legally decisive words: offence names, punishments and
  their limits (e.g. **imprisonment for life**, **not less than seven
  years**), time limits, the courts or authorities involved, and the
  legal test being applied. Bold sparingly: a few phrases per section,
  and never put bold inside bold.
- Write case names in *italics*, e.g. *Bachan Singh v State of Punjab*.
- Use a bulleted list for conditions, ingredients of an offence or
  exceptions; a numbered list for steps in a procedure. Keep each
  bullet to one or two sentences, each with its citation.
- Quote a decisive phrase of a provision with "> " only when its exact
  wording matters.
- Put citations in square brackets immediately after the claim they
  support: [Section 103, BNS], [Article 21], [Bachan Singh v State of Punjab].
  For several, separate with semicolons: [Section 100, BNS; Section 101, BNS].
- Keep the answer under 400 words unless the question is complex.
- End EVERY response with this exact line on its own:
  ⚖ This is a research tool. Verify all provisions against the official
  Gazette. This is not legal advice."""


_SOURCE_HEADERS: Dict[str, str] = {
    "constitution": "CONSTITUTION OF INDIA",
    "bns":          "BHARATIYA NYAYA SANHITA 2023",
    "bnss":         "BHARATIYA NAGARIK SURAKSHA SANHITA 2023",
    "bsa":          "BHARATIYA SAKSHYA ADHINIYAM 2023",
    "ipc":          "INDIAN PENAL CODE 1860 [REPEALED]",
}


def _build_context(documents: List[Dict]) -> str:
    """Pure-Python — synchronous, no I/O."""
    if not documents:
        return ""

    grouped: Dict[str, List[Dict]] = {}
    for doc in documents:
        st = doc.get("source_type", "unknown")
        grouped.setdefault(st, []).append(doc)

    blocks = []
    for source_type, chunks in grouped.items():
        header = _SOURCE_HEADERS.get(source_type, source_type.upper())
        lines  = [f"── SOURCE: {header} ──"]

        for chunk in chunks:
            citation   = chunk.get("citation", "").strip()
            text       = (chunk.get("text") or "").strip()
            status     = chunk.get("status", "active")
            chunk_type = chunk.get("chunk_type", "clause")

            if not text:
                continue

            chunk_lines = []
            if citation:
                chunk_lines.append(f"Citation: {citation}")
            if status != "active":
                chunk_lines.append(f"Status: {status.upper()}")
            if chunk_type == "illustration":
                chunk_lines.append(
                    "Note: The following is a statutory example, not a binding rule."
                )
            chunk_lines.append(f"Text:\n{text}")
            lines.append("\n".join(chunk_lines))

        blocks.append("\n\n".join(lines))

    return "\n\n" + "=" * 60 + "\n\n".join(blocks) + "\n\n" + "=" * 60


def _detect_mode(query: str) -> tuple:
    """Pure-Python — synchronous."""
    q = query.strip()
    if q.upper().startswith("ADVOCATE:"):
        return "advocate", q[len("ADVOCATE:"):].strip()
    if q.upper().startswith("SUMMARISE:"):
        return "summarise", q[len("SUMMARISE:"):].strip()
    return "default", q


class QuestionAnswerer:

    def __init__(self, api_key, settings, modelLoader, faissIndex, queryRewriter):
        self.api_key       = api_key
        self.settings      = settings
        self.modelLoader   = modelLoader
        self.faissIndex    = faissIndex
        self.queryRewriter = queryRewriter
        # Persistent async client for Sarvam API calls.
        # Timeout is generous (90s) — LLM inference on long contexts can be slow.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=10.0),
        )

    async def aclose(self) -> None:
        """Close the connection pool. Called from lifespan shutdown."""
        await self._client.aclose()

    async def generate_answer(self, query: str, documents: List[Dict]) -> str:
        if not documents:
            return (
                "No relevant provisions were retrieved for this query. "
                "Please try rephrasing or narrowing the question.\n\n"
                "⚖ This is a research tool. Verify all provisions against "
                "the official Gazette. This is not legal advice."
            )

        mode, clean_query = _detect_mode(query)
        context           = _build_context(documents)       # sync — pure Python
        user_message      = self._build_user_message(clean_query, mode, context)

        try:
            response = await self._client.post(
                "https://api.sarvam.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.settings.SARVAM_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model": self.settings.SARVAM_MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": user_message},
                    ],
                    "temperature": self.settings.TEMPERATURE_ANSWER,
                    "max_tokens":  8192,
                },
            )

            if response.status_code == 200:
                data          = response.json()
                choice        = data["choices"][0]
                finish_reason = choice.get("finish_reason", "unknown")
                content       = choice.get("message", {}).get("content")

                if content is None:
                    print(f"  WARNING: Sarvam returned content=None")
                    print(f"  finish_reason : {finish_reason}")
                    print(f"  Full response : {data}")
                    return (
                        "The language model returned an empty response. "
                        "This is usually a transient issue — please try again.\n\n"
                        "⚖ This is a research tool. Verify all provisions against "
                        "the official Gazette. This is not legal advice."
                    )

                if finish_reason == "length":
                    print("  WARNING: Sarvam hit max_tokens — answer may be truncated.")

                return self._ensure_disclaimer(content.strip())

            else:
                print(f"  Sarvam API error {response.status_code}: {response.text[:200]}")
                return (
                    f"Sarvam API error {response.status_code}: {response.text}\n\n"
                    "⚖ This is a research tool. Verify all provisions against "
                    "the official Gazette. This is not legal advice."
                )

        except httpx.TimeoutException:
            return (
                "The request to the language model timed out. "
                "Please try again.\n\n"
                "⚖ This is a research tool. Verify all provisions against "
                "the official Gazette. This is not legal advice."
            )
        except Exception as e:
            return (
                f"Error generating answer: {e}\n\n"
                "⚖ This is a research tool. Verify all provisions against "
                "the official Gazette. This is not legal advice."
            )

    def _build_user_message(self, query: str, mode: str, context: str) -> str:
        mode_instructions = {
            "default": (
                "Answer the following legal question precisely and concisely, "
                "citing every claim from the context below."
            ),
            "advocate": (
                "ADVOCATE MODE: Construct the strongest possible counter-argument "
                "to the apparent legal conclusion. Use exceptions, provisos, and "
                "constitutional limits from the context. Make clear this is an "
                "argument, not settled law."
            ),
            "summarise": (
                "SUMMARISE MODE: Explain the answer in plain language for a "
                "non-lawyer client. Avoid jargon. Cite sources in plain English."
            ),
        }
        instruction = mode_instructions.get(mode, mode_instructions["default"])
        return (
            f"{instruction}\n\n"
            f"QUESTION:\n{query}\n\n"
            f"RETRIEVED LEGAL CONTEXT:{context}"
        )

    _DISCLAIMER = (
        "⚖ This is a research tool. Verify all provisions against the official "
        "Gazette. This is not legal advice."
    )

    def _ensure_disclaimer(self, answer: str) -> str:
        if "⚖" in answer:
            return answer
        return answer.rstrip() + "\n\n" + self._DISCLAIMER