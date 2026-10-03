"""
query_rewriter.py
─────────────────
Rewrites user queries into precise legal retrieval fragments via Groq API.

Uses Llama 3.1 8B Instant on Groq — typically responds in under 1 second,
eliminating the 10-second timeout problem from local LM Studio.

Free tier: 14,400 requests/day, 30 requests/minute.
For a legal research tool this is more than sufficient.

Async boundary
──────────────
rewrite_query() is async — awaits a single Groq API call.
Degrades gracefully: on failure returns original query unchanged.
"""

import re
import asyncio
import httpx
from app.core.config import Settings


_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


# ── Per-corpus system prompts ─────────────────────────────────────────

_PROMPT_CONSTITUTION = """You are a legal query rewriter for retrieval in the Constitution of India.

Your job is to ADD precise constitutional vocabulary to the query.
You must KEEP the core subject of the query — never replace it with a description.

RULES:
- Output ONLY a noun phrase — not a sentence, not an answer
- KEEP the main subject word(s) from the original query
- ADD constitutional terms: Article, Part, Schedule, fundamental right, writ, etc.
- Do NOT replace the subject with a generic description
- Do NOT include Article numbers or Part numbers
- Maximum 12 words

EXAMPLES:
  "right to life"        → "right to life fundamental rights Article"
  "freedom of speech"    → "freedom of speech expression fundamental right"
  "emergency powers"     → "emergency proclamation President constitutional powers"
  "can parliament amend" → "constitutional amendment Parliament procedure"
  "schedule 7"           → "Seventh Schedule legislative subjects lists"
"""

_PROMPT_STATUTES = """You are a legal query rewriter for retrieval across three Indian statutes:
- Bharatiya Nyaya Sanhita 2023 (BNS) — criminal offences and punishments
- Bharatiya Nagarik Suraksha Sanhita 2023 (BNSS) — criminal procedure
- Bharatiya Sakshya Adhiniyam 2023 (BSA) — rules of evidence

Your job is to ADD precise statutory vocabulary to the query.
You must KEEP the core subject — never replace it with a generic description.

RULES:
- Output ONLY a noun phrase — not a sentence, not an answer
- KEEP the main crime/concept word(s) from the original query
- ADD at most 3 statutory terms alongside the subject
- Do NOT replace "murder" with "cognizable offence punishable with death"
- Do NOT include section numbers
- Maximum 12 words

EXAMPLES:
  "punishment for murder"    → "murder punishment death imprisonment BNS"
  "how to get bail"          → "bail application bailable offence accused"
  "what is theft"            → "theft dishonest taking moveable property"
  "rape punishment"          → "rape sexual assault punishment imprisonment BNS"
  "confession admissibility" → "confession admissibility accused voluntary BSA"
  "FIR procedure"            → "FIR first information report cognizable offence BNSS"
"""

_PROMPT_JUDGEMENTS = """You are a legal query rewriter for retrieval from Indian Supreme Court judgements.

Your job is to ADD precise case law vocabulary to the query.
You must KEEP any case names, party names, or legal principles in the original query.

RULES:
- Output ONLY a noun phrase — not a sentence, not an answer
- KEEP any case names exactly as written (e.g. "Maneka Gandhi", "Bachan Singh")
- ADD 2-3 legal terms: ratio decidendi, held, constitution bench, landmark, etc.
- ADD relevant constitutional articles if the query implies them
- Do NOT include citation numbers or year brackets
- Do NOT replace case names with generic descriptions
- Maximum 15 words

EXAMPLES:
  "what did supreme court hold in Maneka Gandhi"
      → "Maneka Gandhi ratio decidendi Article 21 personal liberty held"

  "Bachan Singh death penalty rarest of rare"
      → "Bachan Singh rarest of rare death penalty ratio constitution bench"

  "basic structure doctrine"
      → "basic structure doctrine Kesavananda Bharati constitutional amendment ratio"
"""

_PROMPT_ALL = """You are a legal query rewriter for retrieval across Indian law.

Your job is to ADD precise legal vocabulary to the query.
You must KEEP the core subject — never replace it with a generic description.

RULES:
- Output ONLY a noun phrase — not a sentence, not an answer
- KEEP the main subject word(s) from the original query
- ADD 2-3 relevant legal terms from Indian law
- Do NOT replace specific words with generic descriptions
- Do NOT include section or article numbers
- Maximum 12 words

EXAMPLES:
  "punishment for murder"  → "murder punishment death imprisonment BNS"
  "right to silence"       → "right to silence accused self-incrimination"
  "bail for non-bailable"  → "bail non-bailable offence Sessions Court BNSS"
"""


class QueryRewriter:
    """
    Rewrites user queries into legal retrieval fragments via Groq API.
    Degrades gracefully — returns original query if Groq fails.
    """

    _DOMAIN_PROMPTS = {
        "constitution": _PROMPT_CONSTITUTION,
        "statutes":     _PROMPT_STATUTES,
        "judgements":   _PROMPT_JUDGEMENTS,
        "all":          _PROMPT_ALL,
    }

    _STOPWORDS = {
        "what", "which", "when", "where", "does", "that", "this",
        "with", "from", "have", "been", "will", "under", "about",
        "the", "for", "and", "are", "how", "can", "its",
    }

    def __init__(self, settings: Settings, *args, **kwargs):
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def rewrite_query(self, query: str, domain: str = "all") -> str:
        """
        Rewrite a user query into a precise legal retrieval fragment.
        Returns original query if Groq is unavailable or rewriting fails.
        """
        system_prompt = self._DOMAIN_PROMPTS.get(domain, _PROMPT_ALL)
        user_message  = f"Query: {query}\nRewritten:"

        for attempt in range(self.settings.MAX_RETRIES):
            try:
                response = await self._client.post(
                    _GROQ_CHAT_URL,
                    headers={
                        "Authorization": f"Bearer {self.settings.GROQ_API_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model":       self.settings.GROQ_MODEL,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user",   "content": user_message},
                        ],
                        "temperature": self.settings.TEMPERATURE_REWRITE,
                        "max_tokens":  40,
                    },
                )

                if response.status_code == 429:
                    print(f"  QueryRewriter: Groq rate limit, waiting...")
                    await asyncio.sleep(self.settings.RETRY_DELAY * (attempt + 1))
                    continue

                data = response.json()

                if "choices" not in data:
                    print(f"  QueryRewriter: unexpected response: {data}")
                    break

                rewritten = data["choices"][0]["message"]["content"].strip()
                rewritten = self._post_clean(rewritten)

                if not rewritten:
                    return query

                if self._is_replacement(query, rewritten):
                    print(
                        f"  QueryRewriter [{domain}]: rewrite replaced subject "
                        f"— using original ({repr(rewritten)})"
                    )
                    return query

                print(f"  QueryRewriter [{domain}]: {repr(query)} → {repr(rewritten)}")
                return rewritten

            except httpx.TimeoutException:
                print(f"  QueryRewriter: timeout (attempt {attempt + 1})")
            except Exception as e:
                print(f"  QueryRewriter: attempt {attempt + 1} failed: {e}")

            if attempt < self.settings.MAX_RETRIES - 1:
                await asyncio.sleep(self.settings.RETRY_DELAY)

        print("  QueryRewriter: all attempts failed — using original query")
        return query

    def _is_replacement(self, original: str, rewritten: str) -> bool:
        orig_words = {
            w.lower() for w in re.findall(r'\b\w{4,}\b', original)
            if w.lower() not in self._STOPWORDS
        }
        if not orig_words:
            return False
        rew_lower = rewritten.lower()
        return not any(w in rew_lower for w in orig_words)

    def _post_clean(self, text: str) -> str:
        text = text.strip()
        if text.lower().startswith("rewritten:"):
            text = text[len("rewritten:"):].strip()
        if (text.startswith('"') and text.endswith('"')) or \
           (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()
        first_line = text.split('\n')[0].strip()
        first_sent = re.split(r'\.\s+[A-Z]', first_line)[0].strip()
        return first_sent.rstrip('.,;:')