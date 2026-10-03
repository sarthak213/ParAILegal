"""Builds eval/questions.jsonl, the retrieval evaluation set.

Each question lists the provisions that answer it, labelled by citation rather than by
chunk id, so the set survives re-chunking and corpus changes:

    "BNS 103", "BNSS 482", "BSA 63"   statute sections
    "ART 21", "ART SCHEDULE_7"        Constitution articles, schedules, the Preamble
    "CASE maneka_gandhi"              landmark judgments (chunk-id stem)

Grades: 2 = essential (the answer is wrong without it), 1 = supporting.
Questions in "no_answer" have no relevant provisions in the corpus; "corpus_gap"
questions are answerable in law but the provision is missing from the corpus today.

Splits alternate within each category: "dev" for tuning weights and thresholds,
"test" for reporting. Run: python -m eval.build_questions
"""

from __future__ import annotations

import json
from pathlib import Path

E, S = 2, 1  # essential, supporting

QUESTIONS: dict[str, list[tuple[str, dict[str, int]]]] = {
    # ── Direct citations: must be 100% ───────────────────────────────────
    "citation": [
        ("What does Section 103 of the BNS say?", {"BNS 103": E}),
        ("Section 482 BNSS", {"BNSS 482": E}),
        ("Explain Article 21 of the Constitution", {"ART 21": E}),
        ("What is Section 63 of the Bharatiya Sakshya Adhiniyam?", {"BSA 63": E}),
        ("Art. 32", {"ART 32": E}),
        ("BNS 318 cheating", {"BNS 318": E}),
        ("What is laid down in Section 173 of BNSS?", {"BNSS 173": E}),
        ("Article 356", {"ART 356": E}),
        ("Section 80 BNS dowry death", {"BNS 80": E}),
        ("What does Section 23 of the BSA provide?", {"BSA 23": E}),
        ("Article 19(1)(a)", {"ART 19": E}),
        ("s. 35 BNSS", {"BNSS 35": E}),
        ("Section 64 of Bharatiya Nyaya Sanhita", {"BNS 64": E}),
        ("Article 14 equality before law", {"ART 14": E}),
        ("BNSS section 480", {"BNSS 480": E}),
        ("What is Article 368?", {"ART 368": E}),
        ("Section 356 BNS", {"BNS 356": E}),
        ("BSA Section 26", {"BSA 26": E}),
        ("Article 21A", {"ART 21A": E}),
        ("Section 528 BNSS inherent powers", {"BNSS 528": E}),
        ("What does the Seventh Schedule contain?", {"ART SCHEDULE_7": E}),
        ("Preamble to the Constitution of India", {"ART PREAMBLE": E}),
    ],
    # ── Statutory phrases: keyword search's job ──────────────────────────
    "statute_phrase": [
        ("What is the punishment for murder?", {"BNS 103": E, "BNS 101": S}),
        ("Difference between culpable homicide and murder", {"BNS 100": E, "BNS 101": E, "BNS 105": S}),
        ("Punishment for culpable homicide not amounting to murder", {"BNS 105": E}),
        ("Causing death by negligence", {"BNS 106": E}),
        ("Attempt to murder punishment", {"BNS 109": E}),
        ("Abetment of suicide", {"BNS 108": E}),
        ("Definition of theft", {"BNS 303": E}),
        ("What is snatching under BNS?", {"BNS 304": E}),
        ("Extortion", {"BNS 308": E}),
        ("When does theft become robbery?", {"BNS 309": E}),
        ("How many persons are needed for dacoity?", {"BNS 310": E}),
        ("Criminal breach of trust", {"BNS 316": E}),
        ("Dishonest misappropriation of property", {"BNS 314": E}),
        ("Criminal intimidation", {"BNS 351": E}),
        ("Organised crime under BNS", {"BNS 111": E, "BNS 112": S}),
        ("Definition of a terrorist act", {"BNS 113": E}),
        ("Voluntarily causing grievous hurt by acid", {"BNS 124": E}),
        ("What counts as grievous hurt?", {"BNS 116": E, "BNS 117": S}),
        ("Wrongful confinement", {"BNS 127": E}),
        ("Kidnapping for ransom", {"BNS 140": E}),
        ("Trafficking of persons", {"BNS 143": E, "BNS 144": S}),
        ("Gang rape punishment", {"BNS 70": E}),
        ("Sexual intercourse by deceitful means or false promise of marriage", {"BNS 69": E}),
        ("Stalking", {"BNS 78": E}),
        ("Voyeurism", {"BNS 77": E}),
        ("Cruelty by husband or relatives of husband", {"BNS 85": E, "BNS 86": E}),
        ("Promoting enmity between groups on grounds of religion", {"BNS 196": E}),
        ("Acts endangering sovereignty, unity and integrity of India", {"BNS 152": E}),
        ("Rioting", {"BNS 191": E, "BNS 189": S}),
        ("Public nuisance", {"BNS 270": E, "BNS 292": S}),
        ("Giving false evidence", {"BNS 227": E, "BNS 229": S}),
        ("Causing disappearance of evidence of an offence", {"BNS 238": E}),
        ("Criminal conspiracy", {"BNS 61": E}),
        ("Right of private defence of the body extending to causing death", {"BNS 38": E}),
        ("Act of a person of unsound mind", {"BNS 22": E}),
        ("Forgery", {"BNS 336": E, "BNS 335": S}),
        ("When can police arrest without warrant?", {"BNSS 35": E}),
        ("Information in cognizable cases", {"BNSS 173": E}),
        ("Bail in non-bailable offences", {"BNSS 480": E}),
        ("Maximum period an undertrial prisoner can be detained", {"BNSS 479": E}),
        ("Plea bargaining", {"BNSS 290": E, "BNSS 289": S}),
        ("Victim compensation scheme", {"BNSS 396": E}),
        ("Witness protection scheme", {"BNSS 398": E}),
        ("Mercy petition in death sentence cases", {"BNSS 472": E}),
        ("Trial in absentia of a proclaimed offender", {"BNSS 356": E}),
        ("Compounding of offences", {"BNSS 359": E}),
        ("Admissibility of electronic records", {"BSA 63": E, "BSA 61": S}),
        ("Burden of proof", {"BSA 104": E, "BSA 105": S}),
        ("Presumption as to dowry death", {"BSA 118": E}),
        ("Expert opinion as evidence", {"BSA 39": E}),
        ("Leading questions", {"BSA 146": E}),
        ("Testimony of an accomplice", {"BSA 138": E}),
        ("Estoppel", {"BSA 121": E}),
    ],
    # ── Lay language: meaning-based search's job ─────────────────────────
    "lay": [
        ("Can the police arrest me without a warrant?", {"BNSS 35": E}),
        ("The police refused to register my complaint, what can I do?", {"BNSS 173": E, "BNSS 175": S}),
        ("I am afraid I will be arrested, can I get bail in advance?", {"BNSS 482": E}),
        ("How long can police keep me in custody before taking me to a judge?", {"BNSS 58": E, "BNSS 187": S}),
        ("Do the police have to tell me why I am being arrested?", {"BNSS 47": E}),
        ("Will my family be told if I am arrested?", {"BNSS 48": E}),
        ("Can I meet my lawyer while the police question me?", {"BNSS 38": E}),
        ("My wife died within seven years of marriage and her parents allege dowry harassment", {"BNS 80": E, "BSA 118": S}),
        ("My husband's family harasses me for money, what law protects me?", {"BNS 85": E, "BNS 86": E}),
        ("Someone keeps following me and messaging me online", {"BNS 78": E}),
        ("Someone posted lies about me that ruined my reputation", {"BNS 356": E}),
        ("A person threatened to kill me if I go to the police", {"BNS 351": E}),
        ("My employee stole money from the shop", {"BNS 306": E, "BNS 303": S}),
        ("I gave money to a person who promised a job and he disappeared", {"BNS 318": E}),
        ("A man touched a woman inappropriately in a bus", {"BNS 74": E, "BNS 75": S}),
        ("Someone secretly filmed a woman changing clothes", {"BNS 77": E}),
        ("A driver killed a pedestrian by rash driving", {"BNS 106": E, "BNS 281": S}),
        ("A mob of five or more people killed a man because of his caste", {"BNS 103": E}),
        ("Someone threw acid on a woman", {"BNS 124": E}),
        ("My neighbour broke into my house at night", {"BNS 331": E, "BNS 330": S}),
        ("Is a confession made to the police valid in court?", {"BSA 23": E}),
        ("Can WhatsApp chats be used as evidence?", {"BSA 63": E, "BSA 61": S}),
        ("Can a wife be forced to testify against her husband?", {"BSA 128": E, "BSA 126": S}),
        ("Is what I tell my lawyer confidential?", {"BSA 132": E, "BSA 134": S}),
        ("Can a child be a witness in court?", {"BSA 124": E}),
        ("Who has to prove the case in a criminal trial?", {"BSA 104": E, "BSA 105": E}),
        ("Can I defend myself if someone attacks me with a knife?", {"BNS 34": E, "BNS 38": E}),
        ("A seven year old child committed a crime, will he be punished?", {"BNS 20": E}),
        ("I am poor and cannot afford a lawyer for my criminal case", {"BNSS 341": E, "ART 39A": S}),
        ("I have been in jail for years without trial, can I be released?", {"BNSS 479": E}),
        ("How can a husband be made to pay maintenance to his wife and children?", {"BNSS 144": E}),
        ("Can the President pardon someone sentenced to death?", {"ART 72": E}),
        ("Can a person be punished twice for the same crime?", {"ART 20": E, "BNSS 337": E}),
        ("Do I have to answer police questions that incriminate me?", {"ART 20": E}),
        ("Can the government take my land without paying me?", {"ART 300A": E}),
        ("Is free education a right for children?", {"ART 21A": E}),
        ("Can a shop refuse entry to someone because of their caste?", {"ART 15": E, "ART 17": S}),
        ("Can I go directly to the Supreme Court if my fundamental rights are violated?", {"ART 32": E}),
        ("What happens when a state government collapses?", {"ART 356": E}),
        ("Who can change the Constitution and how?", {"ART 368": E}),
    ],
    # ── Old codes (IPC / CrPC / Evidence Act) → new codes ────────────────
    "old_code": [
        ("What was Section 302 IPC and what replaced it?", {"BNS 103": E}),
        ("Section 498A IPC cruelty", {"BNS 85": E, "BNS 86": E}),
        ("Section 420 IPC cheating", {"BNS 318": E}),
        ("Section 304B IPC dowry death", {"BNS 80": E}),
        ("IPC 376 rape punishment", {"BNS 64": E}),
        ("Section 307 IPC attempt to murder", {"BNS 109": E}),
        ("Section 306 IPC", {"BNS 108": E}),
        ("Section 354 IPC outraging modesty of a woman", {"BNS 74": E}),
        ("IPC 506 criminal intimidation", {"BNS 351": E}),
        ("Section 120B IPC criminal conspiracy", {"BNS 61": E}),
        ("What replaced sedition under Section 124A IPC?", {"BNS 152": E}),
        ("Section 34 IPC common intention", {"BNS 3": E}),
        ("Section 304A IPC death by negligence", {"BNS 106": E}),
        ("Section 154 CrPC FIR", {"BNSS 173": E}),
        ("Section 156(3) CrPC magistrate ordering investigation", {"BNSS 175": E}),
        ("Section 438 CrPC anticipatory bail", {"BNSS 482": E}),
        ("Section 439 CrPC special powers of High Court regarding bail", {"BNSS 483": E}),
        ("Section 167 CrPC remand", {"BNSS 187": E}),
        ("Section 125 CrPC maintenance", {"BNSS 144": E}),
        ("Section 482 CrPC inherent powers of High Court", {"BNSS 528": E}),
        ("Section 164 CrPC confession before magistrate", {"BNSS 183": E}),
        ("Section 41A CrPC notice of appearance", {"BNSS 35": E}),
        ("Section 436A CrPC", {"BNSS 479": E}),
        ("Section 65B Evidence Act electronic evidence certificate", {"BSA 63": E}),
        ("Section 25 Evidence Act confession to police", {"BSA 23": E}),
        ("Section 32 Evidence Act dying declaration", {"BSA 26": E}),
        ("Section 113B Evidence Act", {"BSA 118": E}),
        ("Section 106 Indian Evidence Act fact within special knowledge", {"BSA 109": E}),
    ],
    # ── Constitution ─────────────────────────────────────────────────────
    "constitution": [
        ("What freedoms does Article 19 guarantee?", {"ART 19": E}),
        ("Protection against arrest and detention", {"ART 22": E}),
        ("Abolition of untouchability", {"ART 17": E}),
        ("Freedom of religion", {"ART 25": E}),
        ("Equality of opportunity in public employment", {"ART 16": E}),
        ("Fundamental duties of citizens", {"ART 51A": E}),
        ("Writ jurisdiction of High Courts", {"ART 226": E}),
        ("Power of the President to promulgate ordinances", {"ART 123": E}),
        ("Proclamation of national emergency", {"ART 352": E}),
        ("Financial emergency", {"ART 360": E}),
        ("Who appoints the Comptroller and Auditor General?", {"ART 148": E}),
        ("Finance Commission", {"ART 280": E}),
        ("Election Commission powers", {"ART 324": E}),
        ("Advisory jurisdiction of the Supreme Court", {"ART 143": E}),
        ("Equal justice and free legal aid", {"ART 39A": E}),
        ("Special status of Jammu and Kashmir", {"ART 370": E}),
        ("Anti-defection law", {"ART SCHEDULE_10": E}),
        ("Languages recognised in the Constitution", {"ART SCHEDULE_8": E}),
        ("Right to property after the 44th amendment", {"ART 300A": E}),
        ("Protection in respect of conviction for offences", {"ART 20": E}),
    ],
    # ── Landmark judgments ───────────────────────────────────────────────
    "judgement": [
        ("What did the Supreme Court hold in Kesavananda Bharati?", {"CASE kesavananda_bharati": E}),
        ("Basic structure doctrine", {"CASE kesavananda_bharati": E, "CASE minerva_mills": S}),
        ("Ratio of Maneka Gandhi v Union of India", {"CASE maneka_gandhi": E, "CASE menaka_gandhi_article14": E}),
        ("Rarest of rare doctrine for the death penalty", {"CASE bachan_singh": E}),
        ("Is privacy a fundamental right?", {"CASE puttaswamy": E}),
        ("Decriminalisation of homosexuality", {"CASE navtej_johar": E}),
        ("Vishaka guidelines on sexual harassment at the workplace", {"CASE vishaka": E}),
        ("Validity of triple talaq", {"CASE shayara_bano": E}),
        ("Entry of women into Sabarimala temple", {"CASE sabarimala": E}),
        ("Reservation for OBCs and the 50% ceiling", {"CASE indra_sawhney": E}),
        ("When can President's Rule be imposed, according to the Supreme Court?", {"CASE sr_bommai": E, "ART 356": S}),
        ("Right to livelihood of pavement dwellers", {"CASE olga_tellis": E}),
        ("Speedy trial for undertrial prisoners", {"CASE hussainara_khatoon": E}),
        ("Habeas corpus during the Emergency", {"CASE adm_jabalpur": E}),
        ("Can Parliament amend fundamental rights? Golak Nath", {"CASE golak_nath": E}),
        ("A.K. Gopalan preventive detention", {"CASE ak_gopalan": E}),
        ("Bonded labour and public interest litigation", {"CASE bandhua_mukti": E}),
        ("Right to education Unni Krishnan", {"CASE unni_krishnan": E}),
        ("Ganga pollution case", {"CASE mc_mehta_ganga": E}),
        ("Judges transfer case S.P. Gupta", {"CASE sp_gupta": E}),
        ("Champakam Dorairajan communal reservation", {"CASE state_of_madras_champakam": E}),
        ("Election disputes and Article 329 in Ponnuswami", {"CASE t_mm_ponnuswami": E}),
    ],
    # ── Cross-domain ────────────────────────────────────────────────────
    "cross": [
        ("Is the death penalty constitutional and what does BNS say about murder?", {"BNS 103": E, "CASE bachan_singh": E, "ART 21": S}),
        ("Right of an arrested person to know the grounds of arrest under the Constitution and BNSS", {"ART 22": E, "BNSS 47": E}),
        ("Right to free legal aid for accused persons", {"ART 39A": E, "BNSS 341": E, "CASE hussainara_khatoon": S}),
        ("Speedy trial as a fundamental right", {"CASE hussainara_khatoon": E, "ART 21": S}),
        ("Double jeopardy under the Constitution and criminal procedure", {"ART 20": E, "BNSS 337": E}),
        ("Sexual harassment of women at work: law and Supreme Court guidelines", {"CASE vishaka": E, "BNS 75": E}),
        ("Hate speech and freedom of expression", {"ART 19": E, "BNS 196": E}),
        ("Detention beyond 24 hours without a magistrate", {"ART 22": E, "BNSS 58": E}),
        ("Sedition and free speech", {"BNS 152": E, "ART 19": E}),
        ("Self-incrimination: constitutional protection and the evidence law", {"ART 20": E, "BSA 23": S}),
        ("Triple talaq and equality before law", {"CASE shayara_bano": E, "ART 14": S}),
        ("Privacy and the right to life", {"CASE puttaswamy": E, "ART 21": E}),
    ],
    # ── Hindi / Hinglish ─────────────────────────────────────────────────
    "hinglish": [
        ("dhara 302 kya hai", {"BNS 103": E}),
        ("non-bailable case mein zamanat kaise milegi", {"BNSS 480": E}),
        ("dahej hatya ki saza kya hai", {"BNS 80": E}),
        ("498A ka case kya hota hai", {"BNS 85": E, "BNS 86": E}),
        ("agrim zamanat ke liye kahan apply karein", {"BNSS 482": E}),
        ("police FIR darj nahi kar rahi", {"BNSS 173": E}),
        ("kya police bina warrant giraftar kar sakti hai", {"BNSS 35": E}),
        ("हत्या की सजा क्या है", {"BNS 103": E}),
        ("जीवन और व्यक्तिगत स्वतंत्रता का अधिकार", {"ART 21": E}),
        ("चोरी की परिभाषा", {"BNS 303": E}),
    ],
    # ── Typos ────────────────────────────────────────────────────────────
    "typo": [
        ("punishmnet for murdr", {"BNS 103": E}),
        ("anticipatry bail", {"BNSS 482": E}),
        ("defamtion", {"BNS 356": E}),
        ("cheeting and dishonestly inducing delivery", {"BNS 318": E}),
        ("stalkng a woman", {"BNS 78": E}),
        ("artical 21 rite to life", {"ART 21": E}),
        ("electonic evidence admissability", {"BSA 63": E}),
        ("dowery death", {"BNS 80": E}),
        ("presidents rule artcle 356", {"ART 356": E}),
        ("kesavanand bharti case", {"CASE kesavananda_bharati": E}),
    ],
    # ── No answer in the corpus: should abstain ──────────────────────────
    "no_answer": [
        ("What is the limitation period for a civil suit to recover money?", {}),
        ("How do I file for divorce under the Hindu Marriage Act?", {}),
        ("What is the GST rate on restaurant food?", {}),
        ("What documents are needed to incorporate a private limited company?", {}),
        ("Who won the 2011 Cricket World Cup?", {}),
        ("How do I apply for a patent in India?", {}),
        ("How is income tax calculated on salary?", {}),
        ("What does the First Amendment to the US Constitution protect?", {}),
        ("What is the minimum wage in Delhi?", {}),
        ("Explain the doctrine of consideration in contract law", {}),
        ("Can a landlord evict a tenant without notice under rent control law?", {}),
        ("What is the best recipe for biryani?", {}),
    ],
    # ── Answerable in law, but missing from the corpus today ─────────────
    "corpus_gap": [
        ("What does Article 44 say about a uniform civil code?", {"ART 44": E}),
        ("Prohibition of traffic in human beings and forced labour under the Constitution", {"ART 23": E}),
    ],
}

PREFIX = {
    "citation": "CIT", "statute_phrase": "PHR", "lay": "LAY", "old_code": "OLD",
    "constitution": "CON", "judgement": "JDG", "cross": "XDM", "hinglish": "HIN",
    "typo": "TYP", "no_answer": "NOA", "corpus_gap": "GAP",
}


def build() -> list[dict]:
    rows = []
    for category, items in QUESTIONS.items():
        for i, (query, relevant) in enumerate(items, start=1):
            rows.append({
                "id": f"{PREFIX[category]}{i:02d}",
                "category": category,
                "split": "dev" if i % 2 else "test",
                "query": query,
                "relevant": relevant,
            })
    return rows


def main() -> None:
    rows = build()
    path = Path(__file__).with_name("questions.jsonl")
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    by_cat: dict[str, int] = {}
    for row in rows:
        by_cat[row["category"]] = by_cat.get(row["category"], 0) + 1
    print(f"{len(rows)} questions -> {path}")
    for category, n in by_cat.items():
        print(f"  {category:15} {n}")


if __name__ == "__main__":
    main()
