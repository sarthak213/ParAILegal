"""Builds the ToolEval dataset pack for comparing ParAILegal answer models.

    python -m eval.tooleval_pack            # writes eval/tooleval-pack/

Two measures (see ToolEval's README):

  trajectory   test.jsonl. Each conversation is a question, a search_law call, the *correct*
               provisions as the tool result, and a reference answer whose **bold** phrases are
               the key facts. Retrieval is held constant, so only the model varies. ToolEval
               scores grounding (numbers in the answer found in the provisions) and key-fact
               coverage.
  end to end   questions.jsonl. Open questions answered through a real search_law tool loop
               (eval/mcp_server.py), graded by must_include.

Key facts are copied verbatim from the provisions, so a faithful answer contains them.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND / "data"
PACK = Path(__file__).with_name("tooleval-pack")

ABSTAIN = "The provided sources do not contain sufficient information to answer this question."

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_law",
            "description": (
                "Search Indian law (Constitution of India, Bharatiya Nyaya Sanhita, Bharatiya "
                "Nagarik Suraksha Sanhita, Bharatiya Sakshya Adhiniyam, landmark Supreme Court "
                "judgments) and return the most relevant provisions with their citations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The legal question or search terms."},
                    "domain": {
                        "type": "string",
                        "enum": ["constitution", "statutes", "judgements", "all"],
                        "description": "Optional: restrict the search to one area.",
                    },
                },
                "required": ["query"],
            },
        },
    }
]

COMPACT_PROMPT = f"""You are ParAILegal, a legal research assistant for Indian law.

Always call search_law first, then answer ONLY from the provisions it returns.
- Cite every claim in square brackets, e.g. [Section 103, BNS], [Section 482, BNSS], [Section 63, BSA], [Article 21].
- Use the statute's own words for offences, punishments and conditions. Never invent sections, cases or penalties.
- If the provisions do not answer the question, reply exactly: "{ABSTAIN}"
- Keep answers under 200 words. End with: "This is a research tool, not legal advice."
"""

# (id, question, provisions given as context, reference answer, must_include for end to end)
# must_include entries are strings, or lists of acceptable alternatives.
CASES: list[tuple[str, str, list[str], str, list]] = [
    # ── BNS ──────────────────────────────────────────────────────────────
    ("bns-murder", "What is the punishment for murder?", ["BNS 103"],
     "Whoever commits murder shall be punished with **death or imprisonment for life**, and shall also be "
     "liable to **fine** [Section **103**, BNS].",
     ["103", "death", ["imprisonment for life", "life imprisonment"]]),
    ("bns-mob", "What is the punishment when a group of five or more people commits murder on the ground of caste?",
     ["BNS 103"],
     "Each member of the group is punished with death or imprisonment for life or imprisonment of not less than "
     "**seven years**, and is also liable to fine [Section **103**(2), BNS].",
     ["103", ["seven years", "7 years"]]),
    ("bns-dowry", "What is dowry death and how is it punished?", ["BNS 80"],
     "A woman's death by burns, bodily injury or otherwise than under normal circumstances **within seven years "
     "of her marriage**, where soon before her death she was subjected to **cruelty or harassment** for dowry, is "
     "dowry death. It is punished with imprisonment of not less than seven years, which may extend to "
     "**imprisonment for life** [Section **80**, BNS].",
     ["80", ["seven years", "7 years"], ["imprisonment for life", "life imprisonment"]]),
    ("bns-rape", "What is the minimum punishment for rape under the BNS?", ["BNS 64"],
     "Rape is punished with **rigorous imprisonment** for a term not less than **ten years**, which may extend to "
     "imprisonment for life, and fine [Section **64**, BNS].",
     ["64", ["ten years", "10 years"]]),
    ("bns-cheating", "What is the punishment for cheating?", ["BNS 318"],
     "Cheating is punished with imprisonment of either description for a term which may extend to **three "
     "years**, or with fine, or with both [Section **318**(2), BNS].",
     ["318", ["three years", "3 years"]]),
    ("bns-theft", "What is theft?", ["BNS 303"],
     "Whoever, intending to take dishonestly any **movable property** out of the possession of any person "
     "without that person's **consent**, moves that property in order to such taking, commits theft "
     "[Section **303**, BNS].",
     ["303", "movable property", "consent"]),
    ("bns-dacoity", "How many persons are needed for dacoity?", ["BNS 310"],
     "When **five or more persons** conjointly commit or attempt to commit a robbery, each of them commits "
     "dacoity [Section **310**, BNS].",
     ["310", ["five or more", "5 or more"]]),
    ("bns-stalking", "What is the punishment for stalking a woman?", ["BNS 78"],
     "On first conviction, stalking is punished with imprisonment of either description up to **three years** "
     "and fine; on a second or subsequent conviction, up to **five years** and fine [Section **78**, BNS].",
     ["78", ["three years", "3 years"], ["five years", "5 years"]]),
    ("bns-negligence", "What is the punishment for causing death by negligence?", ["BNS 106"],
     "Causing death by a rash or negligent act not amounting to culpable homicide is punished with "
     "imprisonment up to **five years** and fine; for a registered medical practitioner during a medical "
     "procedure, up to **two years** and fine [Section **106**, BNS].",
     ["106", ["five years", "5 years"]]),
    ("bns-abetment-suicide", "What is the punishment for abetment of suicide?", ["BNS 108"],
     "Whoever abets the commission of suicide is punished with imprisonment up to **ten years**, and is also "
     "liable to fine [Section **108**, BNS].",
     ["108", ["ten years", "10 years"]]),
    ("bns-cruelty", "What does cruelty mean in Section 85 of the BNS?", ["BNS 85", "BNS 86"],
     "A husband or his relative who subjects a woman to cruelty is punished with imprisonment up to **three "
     "years** and fine [Section **85**, BNS]. Cruelty means wilful conduct likely to drive her to **commit "
     "suicide** or cause grave injury, or **harassment** to coerce her to meet an unlawful demand for property "
     "[Section **86**, BNS].",
     ["85", "86", ["harassment", "suicide"]]),
    ("bns-child", "Can a six-year-old child be punished for an offence?", ["BNS 20"],
     "No. Nothing is an offence which is done by a **child under seven years of age** [Section **20**, BNS].",
     ["20", ["under seven", "below seven", "under 7"]]),
    # ── BNSS ─────────────────────────────────────────────────────────────
    ("bnss-arrest", "When can the police arrest someone without a warrant?", ["BNSS 35"],
     "A police officer may arrest without an order from a Magistrate and **without a warrant** a person who "
     "commits a **cognizable offence** in the officer's presence, or against whom there is a reasonable "
     "complaint, credible information or reasonable suspicion of a cognizable offence, subject to the "
     "conditions in the section [Section **35**, BNSS].",
     ["35", "cognizable"]),
    ("bnss-grounds", "Must the police tell an arrested person the grounds of arrest?", ["BNSS 47"],
     "Yes. The officer must forthwith communicate **full particulars of the offence** or other grounds of "
     "arrest, and, for a bailable offence, inform the person that he is **entitled to be released on bail** "
     "[Section **47**, BNSS].",
     ["47", ["grounds", "particulars of the offence"]]),
    ("bnss-24h", "How long can police detain a person arrested without warrant before producing him before a magistrate?",
     ["BNSS 58"],
     "Not longer than is reasonable, and in no case more than **twenty-four hours** (excluding journey time) "
     "without a special order of a Magistrate under section 187 [Section **58**, BNSS].",
     ["58", ["twenty-four hours", "24 hours"]]),
    ("bnss-fir", "Can an FIR be given orally or electronically?", ["BNSS 173"],
     "Yes. Information about a cognizable offence may be given **orally or by electronic communication**, "
     "irrespective of where the offence was committed; electronic information must be **signed within three "
     "days** by the informant [Section **173**, BNSS].",
     ["173", ["electronic", "orally"]]),
    ("bnss-undertrial", "When must an undertrial prisoner be released on bail?", ["BNSS 479"],
     "An undertrial who has been detained for **one-half of the maximum period** of imprisonment for the "
     "offence must be released on bail; a first-time offender must be released on bond after **one-third** of "
     "that period. This does not apply to offences punishable with death or life imprisonment "
     "[Section **479**, BNSS].",
     ["479", ["one-half", "half"]]),
    ("bnss-anticipatory", "Which courts can grant anticipatory bail?", ["BNSS 482"],
     "A person who has reason to believe he may be arrested for a non-bailable offence may apply to the **High "
     "Court or the Court of Session**, which may direct that he be released on bail if arrested "
     "[Section **482**, BNSS].",
     ["482", "High Court", ["Court of Session", "Sessions Court"]]),
    ("bnss-maintenance", "Who can claim maintenance under Section 144 BNSS?", ["BNSS 144"],
     "A Magistrate of the first class may order a person with sufficient means to pay a **monthly allowance** to "
     "his **wife** unable to maintain herself, his children, and his **father or mother** unable to maintain "
     "themselves [Section **144**, BNSS].",
     ["144", "wife", ["father or mother", "parents"]]),
    ("bnss-legal-aid", "Does the court provide a lawyer to an accused who cannot afford one?", ["BNSS 341"],
     "Yes. Where the accused is not represented and lacks sufficient means, the Court shall assign an advocate "
     "for his defence **at the expense of the State** [Section **341**, BNSS].",
     ["341", ["expense of the State", "State expense"]]),
    # ── BSA ──────────────────────────────────────────────────────────────
    ("bsa-confession", "Is a confession made to a police officer admissible?", ["BSA 23"],
     "No confession made to a **police officer** shall be proved against an accused, and a confession made in "
     "police custody is not provable unless made in the **immediate presence of a Magistrate**; information "
     "leading to the discovery of a fact may be proved [Section **23**, BSA].",
     ["23", ["not", "no confession"], "Magistrate"]),
    ("bsa-dying", "Is a dying declaration relevant evidence?", ["BSA 26"],
     "Yes. A statement by a person as to the **cause of his death**, or the circumstances of the transaction "
     "resulting in it, is relevant when the cause of death is in question, whether or not he was under "
     "**expectation of death** [Section **26**, BSA].",
     ["26", "cause of"]),
    ("bsa-electronic", "Are electronic records admissible as evidence?", ["BSA 63"],
     "Yes. Information in an electronic record (computer output) is deemed a **document** and is admissible "
     "without production of the original if the section's conditions are met, with a **certificate** "
     "submitted along with the record [Section **63**, BSA].",
     ["63", "certificate"]),
    ("bsa-dowry-presumption", "What presumption applies in a dowry death case?", ["BSA 118"],
     "If it is shown that soon before her death the woman was subjected by the accused to cruelty or harassment "
     "for dowry, the Court **shall presume** that the accused **caused the dowry death** [Section **118**, BSA].",
     ["118", "presume"]),
    ("bsa-spouse", "Can a spouse be compelled to disclose communications made during marriage?", ["BSA 128"],
     "No person who is or has been married shall be **compelled to disclose** any communication made to them "
     "**during marriage** by their spouse, except in suits between them or prosecutions for crimes against "
     "each other [Section **128**, BSA].",
     ["128", "marriage"]),
    # ── Constitution ─────────────────────────────────────────────────────
    ("art-21", "What does Article 21 guarantee?", ["ART 21"],
     "No person shall be deprived of his **life or personal liberty** except according to **procedure "
     "established by law** [Article **21**].",
     ["21", "personal liberty", "procedure established by law"]),
    ("art-20", "Can a person be punished twice for the same offence?", ["ART 20", "BNSS 337"],
     "No. No person shall be prosecuted and punished for the **same offence more than once** [Article **20**(2)], "
     "and a person once convicted or acquitted cannot be **tried again** for the same offence while that "
     "conviction or acquittal remains in force [Section **337**, BNSS].",
     ["20", "337", "same offence"]),
    ("art-22", "What rights does an arrested person have under Article 22?", ["ART 22"],
     "An arrested person must be informed of the **grounds** of arrest, may consult and be defended by a "
     "**legal practitioner of his choice**, and must be produced before the nearest magistrate within "
     "**twenty-four hours** [Article **22**].",
     ["22", "grounds", ["twenty-four hours", "24 hours"]]),
    ("art-32", "What writs can the Supreme Court issue under Article 32?", ["ART 32"],
     "The Supreme Court can issue writs including **habeas corpus, mandamus, prohibition, quo warranto and "
     "certiorari** to enforce fundamental rights [Article **32**].",
     ["32", "habeas corpus", "mandamus", "certiorari"]),
    ("art-72", "Can the President pardon a person sentenced to death?", ["ART 72"],
     "Yes. The President has the power to grant **pardons**, reprieves, respites or remissions, and to "
     "suspend, remit or commute the sentence in all cases where the sentence is a **sentence of death** "
     "[Article **72**].",
     ["72", "pardon"]),
    ("art-21a", "Is education a fundamental right?", ["ART 21A"],
     "Yes. The State shall provide **free and compulsory education** to all children of the age of **six to "
     "fourteen years** [Article **21A**].",
     ["21A", ["free and compulsory", "compulsory education"], ["six to fourteen", "6 to 14"]]),
    # ── Judgments ────────────────────────────────────────────────────────
    ("case-basic-structure", "What is the basic structure doctrine?", ["CASE kesavananda_bharati"],
     "In **Kesavananda Bharati**, the Supreme Court held that Parliament can amend the Constitution under "
     "Article 368 but cannot alter or destroy its **basic structure**; an amendment that does so is void.",
     ["Kesavananda", "basic structure"]),
    ("case-death-penalty", "Is the death penalty constitutional in India?", ["CASE bachan_singh"],
     "Yes. In **Bachan Singh**, the Supreme Court upheld the death penalty for murder but held it may be "
     "imposed only in the **rarest of rare** cases, when life imprisonment is unquestionably foreclosed.",
     ["Bachan Singh", "rarest of rare"]),
    ("case-privacy", "Is privacy a fundamental right?", ["CASE puttaswamy"],
     "Yes. In **Puttaswamy**, the Supreme Court held the right to privacy is a fundamental right protected "
     "under **Article 21**, subject to restrictions that are lawful, pursue a legitimate aim and are "
     "proportionate.",
     ["Puttaswamy", "21"]),
    ("case-speedy-trial", "Is speedy trial a fundamental right?", ["CASE hussainara_khatoon"],
     "Yes. In **Hussainara Khatoon**, the Supreme Court held that the right to speedy trial is implicit in "
     "**Article 21**, and ordered the release of undertrials detained longer than the maximum sentence.",
     ["Hussainara", "21"]),
]

# Off-corpus questions: the tool returns unrelated provisions; the right answer is to abstain.
ABSTAIN_CASES: list[tuple[str, str, list[str]]] = [
    ("noa-divorce", "How do I file for divorce under the Hindu Marriage Act?", ["BNS 82", "BSA 116"]),
    ("noa-gst", "What is the GST rate on restaurant food?", ["BNS 274", "ART 280"]),
    ("noa-patent", "How do I apply for a patent in India?", ["BNS 347", "BNS 345"]),
    ("noa-tenant", "Can a landlord evict a tenant without notice?", ["BNS 329", "BSA 122"]),
]


# ── Corpus access ───────────────────────────────────────────────────────


def load_corpus() -> dict[str, list[dict]]:
    by_ref: dict[str, list[dict]] = defaultdict(list)
    for name, act, title in [
        ("bns_clean.jsonl", "BNS", "Bharatiya Nyaya Sanhita, 2023"),
        ("bnss_clean.jsonl", "BNSS", "Bharatiya Nagarik Suraksha Sanhita, 2023"),
        ("bsa_clean.jsonl", "BSA", "Bharatiya Sakshya Adhiniyam, 2023"),
    ]:
        for line in (DATA / name).open(encoding="utf-8"):
            r = json.loads(line)
            by_ref[f"{act} {r['section_number']}"].append({**r, "_title": title})
    for line in (DATA / "constitution_final.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        by_ref[f"ART {r['article_number']}"].append({**r, "_title": "Constitution of India"})
    for line in (DATA / "landmarks.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        by_ref["CASE " + re.sub(r"_(ratio|held)_\d+$", "", r["chunk_id"])].append(r)
    return by_ref


def provision_text(ref: str, corpus: dict[str, list[dict]]) -> str:
    chunks = corpus[ref]
    kind, _, number = ref.partition(" ")
    if kind == "CASE":
        head = f"{chunks[0]['case_name']}, {chunks[0].get('citation_display') or chunks[0].get('citation')}"
        body = "\n".join(c["text"] for c in chunks)
        return f"[{head}]\n{body}"
    if kind == "ART":
        return f"[Article {number}, Constitution of India]\n" + "\n".join(c["text"] for c in chunks)
    lines = [f"[Section {number}, {kind}: {chunks[0]['_title']}]"]
    for c in chunks:
        label = (c.get("label") or "").strip()
        note = {"illustration": "Illustration: ", "proviso": "", "explanation": ""}.get(c.get("chunk_type"), "")
        lines.append(f"{label} {note}{c.get('text', '').strip()}".strip())
    return "\n".join(lines)


def context(refs: list[str], corpus: dict[str, list[dict]]) -> str:
    return "\n\n".join(provision_text(r, corpus) for r in refs)


# ── Pack ────────────────────────────────────────────────────────────────


def conversation(cid: str, question: str, tool_result: str, reference: str, category: str) -> dict:
    call_id = f"call_{cid}"
    return {
        "id": cid,
        "category": category,
        "messages": [
            {"role": "user", "content": question},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": "search_law", "arguments": json.dumps({"query": question})},
            }]},
            {"role": "tool", "tool_call_id": call_id, "content": tool_result},
            {"role": "assistant", "content": reference},
        ],
    }


def build() -> None:
    corpus = load_corpus()
    missing = [r for _, _, refs, _, _ in CASES for r in refs if r not in corpus]
    missing += [r for _, _, refs in ABSTAIN_CASES for r in refs if r not in corpus]
    if missing:
        sys.exit(f"provisions not in corpus: {missing}")

    if PACK.exists():
        shutil.rmtree(PACK)
    (PACK / "profiles" / "compact").mkdir(parents=True)
    (PACK / "profiles" / "full").mkdir(parents=True)

    conversations, questions = [], []
    for cid, question, refs, reference, must in CASES:
        category = cid.split("-", 1)[0]
        conversations.append(conversation(cid, question, context(refs, corpus), reference, category))
        questions.append({"id": cid, "question": question, "must_include": must, "expected": reference})
    for cid, question, refs in ABSTAIN_CASES:
        conversations.append(conversation(cid, question, context(refs, corpus), ABSTAIN, "abstain"))
        questions.append({"id": cid, "question": question,
                          "must_include": [["sufficient information", "does not contain", "not covered"]],
                          "expected": ABSTAIN})

    def write_jsonl(path: Path, rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    write_jsonl(PACK / "test.jsonl", conversations)
    write_jsonl(PACK / "questions.jsonl", questions)

    tools = json.dumps(TOOLS, indent=1)
    (PACK / "tools.json").write_text(tools, encoding="utf-8")
    for profile in ("compact", "full"):
        (PACK / "profiles" / profile / "tools.json").write_text(tools, encoding="utf-8")
    (PACK / "profiles" / "compact" / "system_prompt.txt").write_text(COMPACT_PROMPT, encoding="utf-8")
    sys.path.insert(0, str(BACKEND))
    from app.infrastructure.llm.answerer import _SYSTEM_PROMPT  # v1's prompt, verbatim

    full = _SYSTEM_PROMPT + "\n\nAlways call search_law first and answer only from what it returns."
    (PACK / "profiles" / "full" / "system_prompt.txt").write_text(full, encoding="utf-8")

    meta = {
        "name": "ParAILegal answers",
        "default_profile": "compact",
        "mcp": {"command": sys.executable, "args": ["-m", "eval.mcp_server"], "cwd": str(BACKEND),
                "env": {"PYTHONUTF8": "1"}},
    }
    (PACK / "dataset.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"{len(conversations)} conversations, {len(questions)} end-to-end questions -> {PACK}")


if __name__ == "__main__":
    build()
