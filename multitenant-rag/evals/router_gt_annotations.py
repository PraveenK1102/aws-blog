"""Semantic ground-truth annotations for the 52 generative questions.

Policy: independent-retrieval-needs-v1.

A question is COMPOUND when answering it requires TWO OR MORE independently
retrievable information needs, such that a focused retrieval query for one need
would not reasonably be expected to retrieve sufficient evidence for the other.
It is SIMPLE when all requested facts belong to ONE localized information need /
entity / event / state reachable by a single focused query. AMBIGUOUS is used
only where reasonable annotators could genuinely disagree.

Applied uniformly via the §5 procedure: (A) identify atomic outputs asked for,
(B) group them by the evidence target each requires, (C) ask whether one focused
query over a localized topic would reasonably retrieve all of them.

Derived from QUESTION TEXT ONLY. Expected answers, router predictions, router
reason codes, baseline scores, judge scores and the previous crude labels were
not inputs to these decisions. Route was not used — it cannot change how many
independent retrieval needs a question has.

Decision rules, stated before the case list so they are applied consistently:
  R1  two unrelated topical domains                        -> COMPOUND
  R2  two distinct events/entities each needing its own
      explanation, from different narrative threads        -> COMPOUND
  R3  N distinct named sibling entities each contributing
      its OWN required value/explanation                   -> COMPOUND (N needs)
  R4  one entity/event/state, several attributes of it     -> SIMPLE
  R5  same entity AND same attribute across time
      (current vs historical)                              -> SIMPLE
  R6  scope / recency / negative / applicability check
      on one fact                                          -> SIMPLE
  R7  one synthesis output spanning a series (one output,
      not N values)                                        -> SIMPLE
"""

# label, independent_retrieval_need_count, atomic_information_needs, rule, reason
A = {
"case-001": ("simple", 1, ["Morrow Bell ringing time and its original purpose"], "R4",
    "One entity (the bell); schedule and origin are attributes of the same subject."),
"case-002": ("simple", 1, ["what Pip-6 actually does, including whether it pollinates"], "R4",
    "One entity; the denial and the real function are the same localized fact."),
"case-003": ("simple", 1, ["Quiet Bloom hours, current value and whether they changed"], "R5",
    "Same entity and same attribute across time; one topical cluster."),
"case-004": ("simple", 1, ["why QL-2D was not approved, including whether charging wait was the cause"], "R4",
    "One object's one approval decision; the second clause tests the same cause."),
"case-005": ("ambiguous", 2, ["why Hollow Stair stayed unverified after Zone South",
                              "evidence supporting each candidate zone"], "R2/R4",
    "'Each candidate' may span two zones' evidence, or may be the same verification narrative."),
"case-006": ("simple", 1, ["Hollow Stair current status, probable location, and the evidence that changed it"], "R4",
    "One state change; status, location and cause are attributes of it."),
"case-007": ("compound", 2, ["what was found inside the T41 box",
                             "evidence connecting T41 to Elian Voss before discovery"], "R2",
    "Post-opening contents and pre-discovery provenance are different evidence targets."),
"case-008": ("compound", 2, ["why QL-2C was unsuitable for Lantern Loop use",
                             "why QL-2D failed passenger approval"], "R3",
    "Two sibling products, each requiring its own rejection explanation."),
"case-009": ("compound", 2, ["charging/stabilization rule for QL-2C",
                             "charging/stabilization rule for QL-2D"], "R3",
    "'Respectively' requires a separate value per product version."),
"case-014": ("simple", 1, ["Silverpine reactor emergency shutdown procedure"], "R4",
    "Single procedure lookup."),
"case-015": ("simple", 1, ["cause of the Ternlink outage at North Fen and records recovered"], "R4",
    "One incident; cause and recovery count are attributes of that event."),
"case-016": ("simple", 1, ["cause of the Ternlink outage at North Fen and records recovered"], "R4",
    "Duplicate of case-015; same label required."),
"case-017": ("simple", 1, ["highest Meral Index ever recorded, with location and date"], "R4",
    "One measurement; value, place and time are its attributes."),
"case-018": ("compound", 2, ["highest Meral Index recorded in the network",
                             "temperature rule for cloud honey in Ember Tea"], "R1",
    "Station measurement and a tea preparation rule are unrelated domains."),
"case-019": ("simple", 1, ["highest Meral Index in the Whisperglass network"], "R4",
    "Single measurement lookup."),
"case-020": ("compound", 2, ["evidence that made Zone North probable",
                             "what was later found inside T41"], "R2",
    "Site-probability reasoning and box contents are different evidence targets."),
"case-021": ("simple", 1, ["contents of the T41 box after opening"], "R4",
    "Single contents lookup."),
"case-022": ("compound", 2, ["highest Meral Index",
                             "why Hollow Stair remained unverified after Zone South"], "R2",
    "Measurement and verification reasoning target different evidence."),
"case-023": ("compound", 2, ["highest Meral Index",
                             "why Hollow Stair remained unverified after Zone South"], "R2",
    "Duplicate of case-022; same label required. Route does not change need count."),
"case-024": ("simple", 1, ["which Quill Cell versions the Forty-Minute Rule applies to"], "R4",
    "One rule; the version breakdown is that rule's content."),
"case-025": ("ambiguous", 2, ["battery currently used by the Lantern Loop fleet",
                              "why QL-2D is not used despite its energy advantage"], "R2/R4",
    "Arguably one battery-selection decision, arguably two separate lookups."),
"case-026": ("simple", 1, ["whether an eastbound hiker should still follow Foxstep"], "R6",
    "Single recency/applicability check."),
"case-027": ("simple", 1, ["whether the Seven-Breath Test applies to a three-litre kettle"], "R6",
    "Single applicability check."),
"case-028": ("simple", 1, ["amount of coffee used in ET-R3"], "R6",
    "Single quantity lookup (likely a negative)."),
"case-029": ("simple", 1, ["whether Hollow Steps and Hollow Stair are the same place"], "R6",
    "Single identity check on one relationship."),
"case-030": ("compound", 3, ["seeds per tray in MS-E1", "seeds per tray in MS-E2",
                             "seeds per tray in MS-E3"], "R3",
    "Three sibling experiments, each contributing its own required value."),
"case-031": ("simple", 1, ["whether MS-E3 can compare Velin-4 against plain water"], "R6",
    "Single methodological check on one experiment."),
"case-032": ("simple", 1, ["why MS-E2 replaced MS-E1 as standard treatment"], "R4",
    "One decision narrative."),
"case-033": ("simple", 1, ["MH-4408's actual pier, vessel and arrival point"], "R4",
    "One shipment's single journey; the fields are attributes of that event chain."),
"case-034": ("simple", 1, ["whether Pier Seven closed because of a ship collision"], "R6",
    "Single causal/negative check."),
"case-035": ("simple", 1, ["whether Harbor Shift Delta is still active"], "R6",
    "Single status check."),
"case-036": ("simple", 1, ["whether WG-03 stopped measuring weather when Ternlink failed"], "R6",
    "Single negative check about one station and one event."),
"case-037": ("simple", 1, ["the three conditions defining a Rillback"], "R4",
    "One definition; the three conditions are its parts."),
"case-038": ("simple", 1, ["whether Rillback thresholds generalise to every station"], "R6",
    "Single scope check."),
"case-039": ("simple", 1, ["whether the network ever recorded Meral 12"], "R6",
    "Single negative check."),
"case-040": ("simple", 1, ["whether the 16:20 bell signals library closing"], "R6",
    "Single interpretation check on one entity."),
"case-041": ("compound", 2, ["why a blue ribbon is appropriate at Sable Library",
                             "why it is inappropriate in Copper Orchard rover zones"], "R1",
    "Library convention and orchard rover safety are unrelated domains."),
"case-042": ("simple", 1, ["how many flowers Pip-6 pollinates per hour"], "R6",
    "Single quantity check (likely a negative)."),
"case-043": ("simple", 1, ["what changed between Pip-6's first trial and 91% performance"], "R4",
    "One entity's progression, one narrative."),
"case-044": ("simple", 1, ["whether BF-233 was cleared for unrestricted handling once scannable"], "R6",
    "Single scope check on one object."),
"case-045": ("simple", 1, ["whether opening T41 moved Hollow Stair to confirmed"], "R6",
    "Single status-change check."),
"case-046": ("compound", 2, ["pressure threshold that sends a Quill Cell to isolation",
                             "korra peel quantity in one ET-R3 flask"], "R1",
    "Battery safety threshold and a tea recipe quantity are unrelated domains."),
"case-047": ("compound", 2, ["current Quiet Bloom window",
                             "what Pip-6 does during that window"], "R2",
    "A schedule and a robot's function are different entities and evidence targets."),
"case-052": ("simple", 1, ["ORIOLE-K crates that reached Kestrel Station Two"], "R4",
    "Single count lookup."),
"case-053": ("simple", 1, ["ORIOLE-K crates that reached Kestrel Station Two"], "R4",
    "Duplicate of case-052; same label required."),
"case-054": ("simple", 1, ["contents of the T41 box"], "R4",
    "Single contents lookup."),
"case-055": ("simple", 1, ["contents of the T41 box"], "R4",
    "Duplicate of case-054; same label required."),
"case-056": ("simple", 1, ["whether an old 07:00 Quiet Bloom end time is still current"], "R6",
    "Single recency check on one fact."),
"case-057": ("simple", 1, ["whether Hollow Stair should still be called unverified"], "R6",
    "Single status check."),
"case-058": ("simple", 1, ["how evidence evolved to the probable Zone North classification"], "R4",
    "One classification's evidence timeline; multiple dates, one narrative."),
"case-059": ("simple", 1, ["a synthesis of how the MS-E series changed what was tested"], "R7",
    "One synthesis output over a series, not N separate required values."),
"case-060": ("simple", 1, ["final results of MS-E4"], "R4",
    "Single results lookup."),
}
