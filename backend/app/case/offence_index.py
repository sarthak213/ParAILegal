"""Search over offences only, for the Case Builder's candidate offences.

The corpus search answers legal questions over 58,000 chunks; an act from a set of facts ("the
husband and his mother beat the wife") matched there finds succession law (mother, children)
before hurt, and nothing links "beat" to "voluntarily causing hurt". This index holds only the
provisions that make something an offence:

  BNS     each section with a row in the BNSS First Schedule (the punishing sections); its
          document carries the Schedule's own name for the offence and the definition it
          punishes ("Punishment for rape" carries section 63, "Rape")
  others  sections of Acts in force that punish ("shall be punished"): the Dowry Prohibition,
          Prevention of Corruption, Arms Acts and the rest, ranked a little below the BNS

An act is searched by keywords (with a small table of everyday words for the legal ones: "beat"
adds "hurt", "took" adds "theft") and by meaning (the corpus's own vectors, offence chunks only),
fused by reciprocal rank.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import numpy as np

from app.case.elements import OFFENCES
from app.case.schedule import Schedule
from app.search.bm25 import BM25Index, FieldWeights
from app.search.engine import ref_of

RRF_K = 30
SPECIAL_ACT_PRIOR = 0.9     # a special Act's offence when the act touches its subject (its title's words)
SPECIAL_ACT_OFF_TOPIC = 0.5  # and when it does not: "the company's money" alone does not make a Companies Act case
_GENERIC_TITLE_WORDS = set("""act acts the and of for in on to with provisions provision special miscellaneous
    prevention prevent protection control regulation regulations development central indian india national
    amendment code powers power procedure general certain matters other public""".split())
BODY_WORDS = 350
FIELDS = FieldWeights(title=3.0, heading=3.0, body=1.0, aliases=1.0)
# a provision that makes something an offence, however the Act words it: "shall be punished",
# "shall, without prejudice to ..., be punished" (NI Act s.138), "deemed to have committed an
# offence", "guilty of an offence", "imprisonment for a term which may extend"
PUNISHES = re.compile(r"\bbe punish(?:ed|able)\b|\bpunishable with\b|\bcommitted an offence\b|\bguilty of an offence\b|"
                      r"\bimprisonment for a term\b|\brigorous imprisonment\b", re.IGNORECASE)
_PUNISHES = PUNISHES

# everyday words -> the words the law uses; added to the act's keywords (not replacing them)
LAY_TERMS: list[tuple[str, str]] = [
    (r"\b(beat|beats|beaten|beating|slap\w*|punch\w*|kick\w*|hit|hits|assault\w*|thrash\w*)\b", "hurt bodily pain criminal force"),
    (r"\b(stab\w*|knife|rod|weapon\w*|acid|sword|axe|lathi|stick)\b", "hurt dangerous weapon grievous"),
    (r"\b(fractur\w*|broke (his|her|their)? ?(arm|leg|bone|teeth|tooth|nose))\b", "grievous hurt fracture"),
    (r"\b(died|death|dead|killed|kill|murder\w*)\b", "death"),
    (r"\b(took|taken|take|stole|stolen|steal\w*|pick-?pocket\w*)\b", "theft dishonestly movable property"),
    (r"\b(snatch\w*)\b", "snatching theft"),
    (r"\b(threat\w*|intimidat\w*)\b", "criminal intimidation threatens"),
    (r"\b(promis\w*|lured|fake|fraud\w*|dup\w*|cheat\w*|deceiv\w*|induc\w*)\b", "cheating deceiving dishonestly induces delivery of property"),
    (r"\b(kept (it |the money |them )?for (himself|herself|themselves)|misappropriat\w*|embezzl\w*|siphon\w*|entrusted)\b", "criminal breach of trust entrusted misappropriates"),
    (r"\b(sexual intercourse|raped|rape|forced sex|sexually assault\w*)\b", "rape sexual intercourse without consent"),
    (r"\b(touch\w*|grop\w*|molest\w*|outrag\w*|disrob\w*)\b", "criminal force woman outrage modesty sexual harassment"),
    (r"\b(follow\w*|stalk\w*|kept messaging|kept calling|watch\w* her)\b", "stalking follows contacts monitors"),
    (r"\b(boy|girl|child|children|minor)\b.*\b(took|taken|away|without)\b|\b(took|taken|away)\b.*\b(boy|girl|child|minor)\b", "kidnapping lawful guardianship minor"),
    (r"\b(locked|confin\w*|detain\w*|kept .* in a room)\b", "wrongful confinement"),
    (r"\b(broke into|broke the lock|break\w* into|burgl\w*)\b", "house-breaking house-trespass"),
    (r"\b(blackmail\w*|demand\w* .*threat\w*|threat\w* .*(pay|money))\b", "extortion"),
    (r"\b(forg\w*|fake (deed|document|signature|certificate))\b", "forgery forged document"),
    (r"\b(defam\w*|reputation|false (statement|allegation)\w*)\b", "defamation imputation harm reputation"),
    (r"\b(drove|driving|drive|rash\w*|speed\w*|negligen\w*)\b", "rash negligent act causing death by negligence driving"),
    (r"\b(hang\w*|suicide|took (her|his) own life|poison\w* (herself|himself))\b", "abetment of suicide"),
    (r"\b(set fire|set .* on fire|petrol|burnt|burned)\b", "mischief by fire"),
    (r"\b(fired|shot at|shoot\w*|pistol|gun|revolver)\b", "attempt to murder firearm"),
    (r"\b(entered|refused to leave|occupied|encroach\w*)\b", "criminal trespass enters property"),
    (r"\b(dowry)\b", "dowry cruelty demand"),
    # a demand of money or goods tied to a marriage is a dowry demand, whether or not the facts say "dowry"
    (r"\b(marri\w*|wedding|groom\w*|bride\w*|in-?laws?)\b.*\b(demand\w*|asked for|insisted)\b|\b(demand\w*|asked for)\b.*\b(marri\w*|wedding|groom\w*|bride\w*)\b",
     "dowry demand"),
    (r"\b(harass\w*|taunt\w*|torment\w*|cruel\w*)\b", "cruelty harassment"),
    (r"\b(bribe\w*|gratification|kickback\w*|accept\w* (rs|money|cash)|(demand\w*|took) .*(rs\.?|money).* to (approve|clear|pass|sign|issue))\b",
     "undue advantage gratification bribe public servant corruption"),
    # whole fields of law, by their everyday words
    (r"\b(ganja|charas|hashish|bhang|marijuana|weed|cannabis)\b", "cannabis narcotic drugs"),
    (r"\b(heroin|smack|brown sugar|opium|cocaine|morphine|mdma|narcotic\w*|drugs?)\b", "narcotic drug psychotropic substance"),
    (r"\b(pistol|revolver|gun|rifle|firearm\w*|cartridge\w*|ammunition|bullets?)\b", "arms firearm ammunition licence"),
    (r"\b(cheque\w*|check bounce\w*|bounced|dishonour\w*)\b", "dishonour of cheque insufficiency of funds negotiable instruments"),
    (r"\b(online|otp|computer|internet|e-?mail|website|app|social media|facebook|instagram|whatsapp|hack\w*|password|account)\b",
     "computer resource electronic information technology"),
    (r"\b(five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|\d{1,3}|group of|mob|gang|crowd)\b.*\b(men|persons|people|accused|members)\b|\b(mob|gang|crowd)\b",
     "five or more persons conjointly unlawful assembly rioting dacoity"),
    (r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen)[- ]year[- ]old\b|\b(minor|child|girl|boy)\b",
     "child minor children"),
    (r"\b(recorded|filmed|photograph\w*|camera|video\w*)\b.*\b(bath\w*|chang\w*|undress\w*|privat\w*)\b", "voyeurism private act captures image"),
    (r"\b(obscene|lewd|vulgar|whistl\w*|gestur\w*)\b", "insult modesty word gesture obscene"),
    (r"\b(acid)\b", "acid grievous hurt"),
    (r"\b(abet\w*|instigat\w*|conspir\w*|aided)\b", "abetment conspiracy"),
]

# sections that make abetting, conspiring or attempting any offence punishable (BNS 49-62): listed
# only when the facts speak of abetting or conspiring, since every act would otherwise match them
GENERAL = {f"BNS {n}" for n in range(45, 63)}
_ABETS = re.compile(r"\b(abet\w*|instigat\w*|conspir\w*|aided)\b", re.IGNORECASE)


def lay_terms(act: str) -> str:
    low = act.lower()
    return " ".join(extra for pattern, extra in LAY_TERMS if re.search(pattern, low))


def _subject(title: str) -> set[str]:
    """"The Prevention of Corruption Act, 1988" -> {"corru"}; "The Arms Act, 1959" -> {"arms"}."""
    words = re.findall(r"[a-z]{4,}", title.lower())
    return {w[:5] for w in words if w not in _GENERIC_TITLE_WORDS}


def _key(title: str) -> str:
    return re.sub(r"[^a-z ]+", " ", title.lower()).strip()


class OffenceIndex:
    def __init__(self, engine: Any, schedule: Schedule) -> None:
        self.engine = engine
        by_ref: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(engine.chunks):
            by_ref[ref_of(c)].append(i)

        def title(ref: str) -> str:
            first = engine.chunks[by_ref[ref][0]]
            return (first.get("section_title") or (first.get("citation") or "").split("\n")[0].split("—")[-1]).strip(" .")

        def text(ref: str) -> str:
            return " ".join(engine.chunks[i].get("text") or "" for i in by_ref[ref])

        # BNS definitions without a Schedule row ("Rape", "Murder"), by title, for the section that punishes them
        bns = [r for r in by_ref if r.startswith("BNS ")]
        definitions = {_key(title(r)): r for r in bns if not schedule.classify(r.split()[1]) and r not in OFFENCES}
        self.refs: list[str] = []
        self.prior: list[float] = []
        self.subject: list[set[str]] = []  # a special Act's distinctive title words, as 5-letter stems
        self.chunks_of: list[list[int]] = []
        docs = []
        for ref in bns + sorted(r for r in by_ref if not r.startswith(("BNS ", "BNSS ", "BSA ", "ART ", "CASE ", "IPC ", "CRPC ", "IEA "))):
            act, _, number = ref.rpartition(" ")
            first = engine.chunks[by_ref[ref][0]]
            if act == "BNS":
                rows = schedule.classify(number)
                if not rows and ref not in OFFENCES:
                    continue
                heading = " ".join(dict.fromkeys(r.offence for r in rows))
                t = _key(title(ref))
                defined = [d for k, d in definitions.items() if k and (t == f"punishment for {k}" or
                                                                       t.startswith(f"punishment for {k} "))]
                chunk_ids = [i for r in [ref, *defined] for i in by_ref[r]]
                body = " ".join([*(text(d) for d in defined), text(ref)])
                prior = 1.0
            else:
                if (number.upper().startswith("SCHEDULE") or (first.get("status") or "").lower() == "repealed"
                        or not _PUNISHES.search(text(ref))):
                    continue  # a schedule is a table, not a provision that creates an offence
                heading, chunk_ids, body, prior = first.get("document_title") or "", by_ref[ref], text(ref), SPECIAL_ACT_PRIOR
            self.refs.append(ref)
            self.prior.append(prior)
            self.subject.append(_subject(heading) if act != "BNS" else set())
            self.chunks_of.append(chunk_ids)
            docs.append({"title": title(ref), "heading": heading, "body": " ".join(body.split()[:BODY_WORDS]),
                         "aliases": ""})
        self.bm25 = BM25Index(docs)
        self._dense_rows()

    def _dense_rows(self) -> None:
        """Each offence's rows in the corpus's dense vectors (windows of its chunks)."""
        dense = getattr(self.engine, "dense", None)
        self.vec_rows = None
        if dense is None or not hasattr(dense, "vectors"):
            return
        owner_of_chunk: dict[int, int] = {}
        for k, chunk_ids in enumerate(self.chunks_of):
            for i in chunk_ids:
                owner_of_chunk.setdefault(i, k)
        rows = [(r, owner_of_chunk[int(o)]) for r, o in enumerate(dense.owner) if int(o) in owner_of_chunk]
        self.vec_rows = np.array([r for r, _ in rows])
        self.vec_owner = np.array([k for _, k in rows])

    def _meaning(self, act: str, limit: int) -> list[int]:
        if self.vec_rows is None or not len(self.vec_rows):
            return []
        dense = self.engine.dense
        q = np.array(next(iter(dense.model.embed([dense.spec.query_prefix + act]))), dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-12
        sims = dense.vectors[self.vec_rows] @ q
        best = np.full(len(self.refs), -np.inf, dtype=np.float32)
        np.maximum.at(best, self.vec_owner, sims)
        return [int(k) for k in np.argsort(-best)[:limit] if np.isfinite(best[k])]

    def search(self, act: str, k: int = 10, pool: int = 60) -> list[tuple[str, float]]:
        """(ref, score) for one act, best first."""
        words = re.findall(r"[a-z][a-z-]{2,}", f"{act} {lay_terms(act)}".lower())
        keyword = [i for i, _ in self.bm25.search(list(dict.fromkeys(words)), limit=pool, weights=FIELDS)]
        meaning = self._meaning(act, pool)
        score: dict[int, float] = defaultdict(float)
        general_ok = bool(_ABETS.search(act))
        for ranked in (keyword, meaning):
            for rank, i in enumerate(ranked):
                if self.refs[i] in GENERAL and not general_ok:
                    continue
                score[i] += 1 / (RRF_K + rank + 1)
        stems = {w[:5] for w in words}
        prior = {i: self.prior[i] if not self.subject[i] or self.subject[i] & stems else SPECIAL_ACT_OFF_TOPIC
                 for i in score}
        best = sorted(score, key=lambda i: -score[i] * prior[i])[:k]
        return [(self.refs[i], score[i] * prior[i]) for i in best]
