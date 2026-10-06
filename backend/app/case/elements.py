"""Elements of common offences: what the facts must show for each, checked one by one.

Written by hand from the statute text. Every element carries `quote`, the words of the section it
comes from, copied exactly; tests/test_case_elements.py checks each quote against the corpus, so
an element can never drift from the law it states. `source` names the section the quote is in
when it is not the offence's own section (murder's elements are in the definitions, s.100-101).

Kinds:
  element    must be shown for the offence
  any_of     one of a group must be shown (elements sharing a `group` name)
  aggravation  makes a heavier form of the offence (a separate charge or sub-section)

DRAFT: these tables need review by a practising lawyer before release (docs/ROADMAP-v2.md,
Case Builder ground rules).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Element:
    label: str          # what the facts must show, in plain words
    quote: str          # the statute's own words, copied exactly
    source: str = ""    # the provision quoted, if not the offence's own ("BNS 101")
    kind: str = "element"
    group: str = ""     # for any_of: elements sharing a group are alternatives


@dataclass(frozen=True)
class Offence:
    ref: str            # "BNS 103": the provision that punishes it
    name: str
    elements: tuple[Element, ...]


def E(label: str, quote: str, source: str = "", kind: str = "element", group: str = "") -> Element:
    return Element(label, quote, source, kind, group)


OFFENCES: dict[str, Offence] = {o.ref: o for o in (
    Offence("BNS 103", "Murder", (
        E("The accused caused the death of a person", "Whoever causes death by doing an act", "BNS 100"),
        E("The act was done with the intention of causing death", "done with the intention of causing death",
          "BNS 101", "any_of", "mental state"),
        E("…or intending bodily injury the accused knew was likely to cause that person's death",
          "intention of causing such bodily injury as the offender knows to be likely to cause the death",
          "BNS 101", "any_of", "mental state"),
        E("…or intending bodily injury that is sufficient in the ordinary course of nature to cause death",
          "sufficient in the ordinary course of nature to cause death", "BNS 101", "any_of", "mental state"),
        E("…or knowing the act was so imminently dangerous it would in all probability cause death, with no excuse",
          "so imminently dangerous that it must, in all probability, cause death", "BNS 101", "any_of", "mental state"),
        E("None of the exceptions to murder applies (e.g. grave and sudden provocation, private defence)",
          "Except in the cases hereinafter excepted, culpable homicide is murder", "BNS 101"),
    )),
    Offence("BNS 105", "Culpable homicide not amounting to murder", (
        E("The accused caused the death of a person", "Whoever causes death by doing an act", "BNS 100"),
        E("With the intention of causing death", "with the intention of causing death", "BNS 100", "any_of", "mental state"),
        E("…or intending bodily injury likely to cause death",
          "with the intention of causing such bodily injury as is likely to cause death", "BNS 100", "any_of", "mental state"),
        E("…or knowing the act was likely to cause death",
          "with the knowledge that he is likely by such act to cause death", "BNS 100", "any_of", "mental state"),
    )),
    Offence("BNS 106", "Causing death by negligence", (
        E("The accused caused the death of a person", "Whoever causes death of any person"),
        E("By a rash or negligent act", "by doing any rash or negligent act"),
        E("The act does not amount to culpable homicide", "not amounting to culpable homicide"),
    )),
    Offence("BNS 115", "Voluntarily causing hurt", (
        E("The accused caused bodily pain, disease or infirmity to a person",
          "Whoever causes bodily pain, disease or infirmity to any person", "BNS 114"),
        E("Intending to cause hurt, or knowing hurt was likely",
          "with the intention of thereby causing hurt to any person, or with the knowledge that he is likely thereby to cause hurt"),
    )),
    Offence("BNS 117", "Voluntarily causing grievous hurt", (
        E("The hurt caused is grievous: e.g. fracture, permanent loss of sight or hearing, permanent disfigurement, "
          "or 15 days of severe pain", "Fracture or dislocation of a bone or tooth", "BNS 116"),
        E("The accused intended, or knew it was likely, to cause grievous hurt",
          "if the hurt which he intends to cause or knows himself to be likely to cause is grievous hurt"),
    )),
    Offence("BNS 303", "Theft", (
        E("Movable property", "any movable property"),
        E("Taken out of the possession of another person", "out of the possession of any person"),
        E("Without that person's consent", "without that person’s consent"),
        E("With intent to take it dishonestly", "intending to take dishonestly"),
        E("The property was moved in order to take it", "moves that property in order to such taking"),
    )),
    Offence("BNS 308", "Extortion", (
        E("The accused intentionally put a person in fear of injury",
          "Whoever intentionally puts any person in fear of any injury to that person, or to any other"),
        E("And thereby dishonestly induced delivery of property or a valuable security",
          "thereby dishonestly induces the person so put in fear to deliver to any person any property"),
    )),
    Offence("BNS 309", "Robbery", (
        E("There was theft or extortion", "In all robbery there is either theft or extortion"),
        E("For that end, the accused caused or attempted death, hurt or wrongful restraint, or fear of them",
          "voluntarily causes or attempts to cause to any person death or hurt or wrongful restraint", "", "any_of", "violence"),
        E("…or, in extortion, was present and put the person in fear of instant death, hurt or restraint",
          "is in the presence of the person put in fear", "", "any_of", "violence"),
    )),
    Offence("BNS 316", "Criminal breach of trust", (
        E("The accused was entrusted with property, or with dominion over it",
          "being in any manner entrusted with property, or with any dominion over property"),
        E("And dishonestly misappropriated it or converted it to their own use",
          "dishonestly misappropriates or converts to his own use that property", "", "any_of", "breach"),
        E("…or dishonestly used or disposed of it against the law or the terms of the trust",
          "dishonestly uses or disposes of that property in violation of any direction of law", "", "any_of", "breach"),
    )),
    Offence("BNS 318", "Cheating", (
        E("The accused deceived a person (dishonest concealment of facts counts)", "by deceiving any person"),
        E("And fraudulently or dishonestly induced them to deliver property, or to let someone keep it",
          "fraudulently or dishonestly induces the person so deceived to deliver any property to any person",
          "", "any_of", "inducement"),
        E("…or intentionally induced an act or omission that caused or was likely to cause them harm",
          "causes or is likely to cause damage or harm to that person in body, mind, reputation or property",
          "", "any_of", "inducement"),
        E("Delivery of property induced by cheating (heavier offence, s.318(4))",
          "dishonestly induces the person deceived to deliver any property to any person", "", "aggravation"),
    )),
    Offence("BNS 351", "Criminal intimidation", (
        E("The accused threatened injury to a person, their reputation or property (or to someone they care about)",
          "threatens another by any means, with any injury to his person, reputation or property"),
        E("With intent to cause alarm, or to make them do or not do something", "with intent to cause alarm to that person"),
        E("A threat to cause death or grievous hurt, or to burn property (heavier, s.351(3))",
          "by threatening to cause death or grievous hurt", "", "aggravation"),
    )),
    Offence("BNS 329", "Criminal trespass", (
        E("The accused entered property in another's possession, or unlawfully remained after lawful entry",
          "enters into or upon property in the possession of another"),
        E("With intent to commit an offence, or to intimidate, insult or annoy the person in possession",
          "with intent to commit an offence or to intimidate, insult or annoy any person in possession of such property"),
        E("In a building used as a dwelling, place of worship or for custody of property (house-trespass, s.329(2))",
          "any building, tent or vessel used as a human dwelling", "", "aggravation"),
    )),
    Offence("BNS 85", "Cruelty by husband or his relatives", (
        E("The victim is a woman, and the accused is her husband or a relative of her husband",
          "being the husband or the relative of the husband of a woman"),
        E("Wilful conduct likely to drive her to suicide or cause grave injury or danger to life, limb or health",
          "any wilful conduct which is of such a nature as is likely to drive the woman to commit suicide",
          "BNS 86", "any_of", "cruelty"),
        E("…or harassment to coerce her or her relatives to meet an unlawful demand for property (e.g. dowry)",
          "with a view to coercing her or any person related to her to meet any unlawful demand for any property",
          "BNS 86", "any_of", "cruelty"),
    )),
    Offence("BNS 80", "Dowry death", (
        E("A woman died of burns or bodily injury, or otherwise than in normal circumstances",
          "the death of a woman is caused by any burns or bodily injury or occurs otherwise than under normal circumstances"),
        E("Within seven years of her marriage", "within seven years of her marriage"),
        E("Soon before her death she was subjected to cruelty or harassment by her husband or his relative",
          "soon before her death she was subjected to cruelty or harassment by her husband or any relative of her husband"),
        E("In connection with a demand for dowry", "in connection with, any demand for dowry"),
    )),
    Offence("BNS 74", "Assault or criminal force to outrage a woman's modesty", (
        E("The accused assaulted or used criminal force on a woman", "assaults or uses criminal force to any woman"),
        E("Intending to outrage her modesty, or knowing it was likely to",
          "intending to outrage or knowing it to be likely that he will thereby outrage her modesty"),
    )),
    Offence("BNS 78", "Stalking", (
        E("The accused, a man, followed a woman and contacted or tried to contact her repeatedly, despite her clear "
          "disinterest", "follows a woman and contacts, or attempts to contact such woman to foster personal interaction repeatedly",
          "", "any_of", "conduct"),
        E("…or monitored her use of the internet, e-mail or other electronic communication",
          "monitors the use by a woman of the internet, e-mail or any other form of electronic communication",
          "", "any_of", "conduct"),
        E("Despite a clear indication of disinterest by her", "despite a clear indication of disinterest by such woman"),
    )),
    Offence("negotiable_instruments_act_1881 138", "Dishonour of cheque", (
        E("A cheque drawn on an account maintained by the accused", "drawn by a person on an account maintained by him with a banker"),
        E("Given to discharge a debt or other liability, in whole or in part",
          "for the discharge, in whole or in part, of any debt or other liability"),
        E("Returned unpaid for insufficient funds, or for exceeding the arranged amount", "is returned by the bank unpaid"),
        E("Presented within six months of its date, or within its validity", "within a period of six months from the date on which it is drawn"),
        E("A written demand notice sent within thirty days of learning of the dishonour",
          "of the receipt of information by him from the bank regarding the return of the cheque as unpaid"),
        E("The drawer did not pay within fifteen days of receiving the notice",
          "fails to make the payment of the said amount of money to the payee"),
    )),
)}


def for_ref(ref: str) -> Offence | None:
    return OFFENCES.get(ref)
