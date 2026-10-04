# RAG Evaluation Results — Tests 24–60 (Claude RAG Evaluation Suite)

**Run:** live prod (https://d261g450savmee.cloudfront.net), seeded corpus `seed-20260822` (6 profiles, 4 groups, 50 posts).
**Models:** retrieval Titan V2 + BM25 (Qdrant RRF); generation Groq gpt-oss-120b (single/group), gpt-oss-20b (overview).

## Methodology & caveats
- Scope mapped exactly: single→/api/ask, multi→/api/ask/group (tenant_ids), group→/api/ask/group (group_id), global→/api/search/global.
- **9 tests are BLOCKED (generation) by Groq free-tier rate-limit saturation (HTTP 429)** from the eval's own volume — NOT app defects. Retrieval succeeded for them (citations captured); they need a re-run after cooldown or a paid Groq tier.
- **Group scope is membership-based** (union of member tenants' ALL posts), not per-post 'Group' tag. Overlapping membership can leak content across groups (see test 52).
- **Encoding (UI-001): mojibake present in ~all generated answers** (em-dash/quotes as `â...`). Answers shown **as-is** below — the `â` sequences ARE the UI-001 defect; counted once as systemic.
- Ground truth NOT modified to match output.

## Aggregate metrics (28 scored; 9 blocked excluded)
- Average: **8.2/10** over 28 scored.
- PASS **18/28** (64%) · PARTIAL **7/28** (25%) · FAIL **3/28** (11%).
- Hallucination (invented facts): **0**. Generation scope-leak (scored): **0** (test 54 refused correctly). Retrieval scope-leak: **1** (test 52, membership overlap).
- Unsupported-citation: low (global test 49 near-name noise). Encoding/UI mojibake: **systemic (~all answers)**.
- **By mode:** single-profile ~8.6/10 (strong); multi-profile 1/1 scored PASS (2 blocked); group ~8.25/10 over 8 scored (5 more blocked); global ~6.25/10 (weakest).
- **Global retrieval:** Hit@1 = 3/3 on defined-top tests (48,49,51); MRR≈1.0; recall moderate (49≈0.5, 51≈0.33); negative test 50 FAIL (no no-result threshold).
- **Strengths:** exact-ID retrieval (37,39,48), scope refusals (28,42,54), single-fact accuracy.
- **Weaknesses:** compound completeness (24,30,34,43), temporal-update retrieval (26,45 fell to clarify), global no-result threshold (50) + recall (51).

## Per-test results

### 24 — single:Dev Iyer  (1.1s)
- **Q:** Does the Forty-Minute Rule still apply to the newest Quill Cell? Explain which versions require 40 minutes and which do not.
- **Expected:** QL-2B and QL-2C require 40 min. QL-2D uses Tessafilm, 12-min stabilization.
- **Actual:** Yesâthe FortyâMinute Rule still applies to the newest Quill Cell that Dev Iyer has documented. In *Quill Cells and the FortyâMinute Rule* he notes that after a highâload discharge the QLâ2B cell ârequired forty minutes of rest before fast charging.â In the followâup post *Quill Cell Revision QLâ2C* he explicitly states that âThe FortyâMinute Rule still appliedâ to the QLâ2C version. No other versions are mentioned as exempt, so both QLâ2B and QLâ2C require the 40âminute rest period.
- **Citations:** ['Quill Cells and the Forty-Minute Rule', 'Quill Cell Revision QL-2C']
- **Score: 7/10 — PARTIAL.** Got QL-2B/QL-2C=40min but MISSED QL-2D's 12-min Tessafilm exception ('no other versions exempt'). Cause: completeness / compound-retrieval — QL-2D post not surfaced in single-profile scope.

### 25 — single:Arjun Vale  (1.0s)
- **Q:** Which battery is currently used by the Lantern Loop fleet, and why isn't QL-2D used despite its energy advantage?
- **Expected:** LumenPack 8. QL-2D ~14% less energy but 11cm taller enclosure blocks manual brake-release lever.
- **Actual:** The fleet is still using the **LumenPackâ¯8** battery (the post notes that âLumenPackâ¯8 remained the active fleet batteryâ)ãLantern Loop Tests QL-2Dã.  

QLâ2D, even though it consumes aboutâ¯14â¯percent less energy, was not cleared for passenger service because its enclosure is elevenâ¯centimetres taller, which blocks access to the manual brakeârelease leverâa safety issue that disqualified itãLantern Loop Tests QL-2Dã.
- **Citations:** ['Lantern Loop Tests QL-2D']
- **Score: 10/10 — PASS.** LumenPack 8 + QL-2D ~14% energy + 11cm enclosure blocks brake lever. Fully correct.

### 26 — single:Kian Rao  (1.0s)
- **Q:** Should an eastbound hiker still follow Foxstep at Red Compass Trail?
- **Expected:** No. RC-14 replaced 4 May; Foxstep retired; use main trail/new red post.
- **Actual:** Kian Rao (seedâ20260822) hasnât written about whether Foxstep should still be followed today, but he does discuss Foxstep as a temporary navigation rule that applied only to eastbound hikers on the Redâ¯Compassâ¯Trail (see his post *The Red Compass Trail Rule*). Would you like more details on what that rule entailed?
- **Citations:** ['The Red Compass Trail Rule']
- **Score: 5/10 — FAIL.** Temporal miss: returned a clarify ('hasn't written whether to follow today') instead of the update (RC-14 replaced 4 May; Foxstep retired). Cause: the retirement post didn't clear the retrieval floor -> fell to overview.

### 27 — single:Tara Moss  (1.1s)
- **Q:** Can the Seven-Breath Test be used for a three-liter kettle of Ember Tea?
- **Expected:** No. Only 600ml pots. For 3L use thermometer / wait to 68-70C.
- **Actual:** No.â¯In *A Correction About the SevenâBreath Test*, Tara Moss notes that the SevenâBreath Test was intended only for 600â¯ml expedition pots and should **not** be applied to a threeâliter kitchen kettle. For large kettles, she recommends using a thermometer or waiting until the temperature reaches 68â¯ââ¯70â¯°F.
- **Citations:** ['A Correction About the Seven-Breath Test']
- **Score: 9/10 — PASS.** Correct: 600ml only; use thermometer / 68-70. Minor unit slip (said F, expected C).

### 28 — single:Tara Moss  (2.9s)
- **Q:** How much coffee is used in ET-R3?
- **Expected:** Refuse/no evidence; do not invent coffee.
- **Actual:** Taraâ¯Moss (seedâ20260822) hasnât written about coffee inâ¯ETâR3. The closest material sheâs provided is the detailed ingredient list for Emberâ¯Teaâ¯Versionâ¯Three (e.g., nineâ¯grams of dried korra peel, threeâ¯grams of crushed mintroot, oneâ¯spoon of cloud honey, twoâ¯flakes of black salt, and 600â¯ml of hot water). Would you like a summary of those ingredients instead?
- **Citations:** ['Ember Tea Version Three']
- **Score: 10/10 — PASS.** Negative handled: did NOT invent coffee; stated no coffee in ET-R3, offered real ingredients.

### 29 — group:Field Circle  (2.7s)
- **Q:** Are Hollow Steps and Hollow Stair now believed to be the same place?
- **Expected:** No. Zone North=probable Hollow Stair; Zone South=separate 1984 Hollow Steps route.
- **Actual:** Kianâ¯Rao makes it clear that the two features are being treated as distinct. In the update on the Hollowâ¯Stair status, Rao notes that âZoneâ¯North became the probable Hollowâ¯Stairâ and that âZoneâ¯South was reinterpreted as the separateâ¯Hollowâ¯Steps route.ââ¯Similarly, Nilaâ¯Royâs expedition brief distinguishes the two areas: âZoneâ¯North corresponded to theâ¯1991 ranger noteâ (the Hollowâ¯Stair ridge) while âZoneâ¯South covered theâ¯1984â¯Hollowâ¯Steps location.ââ¯Together these posts indicate that, at present, Hollowâ¯Steps and Hollowâ¯Stair are not 
- **Citations:** ['The Hollow Stair Status Finally Changes', 'The Lake Tressa Disagreement', 'A Hollow Stair Expedition Is Approved']
- **Score: 10/10 — PASS.** Correct: Hollow Steps vs Hollow Stair are distinct (Zone North=Stair, Zone South=1984 Steps).

### 30 — group:Orchid Lab  (2.4s)
- **Q:** How many seeds per tray were used in MS-E1, MS-E2 and MS-E3?
- **Expected:** 18, 18, 16.
- **Actual:** Arjunâ¯Vale records that, in the MSâE2 experiment, each tray held **eighteen seeds**.â¯Miraâ¯Sen reinforces this by noting that **both MSâE1 and MSâE2 retained eighteen seeds per tray**.â¯None of the excerpts mention a seed count for an MSâE3 experiment, so that information is not provided in this selection.
- **Citations:** ['Starting MS-E2', 'Moonseed Day Twenty']
- **Score: 6/10 — PARTIAL.** Got E1/E2=18 but MISSED E3=16 ('MS-E3 not mentioned'). Cause: compound completeness — E3 seed-count not surfaced.

### 31 — group:Orchid Lab  (2.0s)
- **Q:** Can MS-E3 tell us whether Velin-4 performs better than plain water?
- **Expected:** No. All trays used standard diluted Velin-4; no untreated control; E3 tested Greyveil/light timing.
- **Actual:** Miraâ¯Sen notes that the MSâE3 experiment was unable to determine whether Velinâ4 outperforms plain water. In her field notes she writes that âMSâE3 could not answer whether Velinâ4 beats plain water.â
- **Citations:** ['MS-E3 Will Test Light, Not Minerals']
- **Score: 8.5/10 — PASS.** Correct conclusion (E3 can't compare Velin-4 vs water); thin on the 'why' (no untreated control).

### 32 — group:Orchid Lab  (2.5s)
- **Q:** Why did MS-E2 become the standard treatment instead of MS-E1?
- **Expected:** E1 strong germination/stems, fewer side leaves. E2 75% conc improved balance (75% germ, 14.1cm, 6.9 side leaves vs 7.1 control, higher stem width).
- **Actual:** Nilaâ¯Roy explains that the MSâE2 formulation outperformed the earlier MSâE1 protocol and was therefore adopted as the default.  In her âMSâE2 Final Resultsâ post she notes that the treated plants achieved a 75â¯percent germination rate, grew to an average height of 14.1â¯centimetres and showed favorable sideâleaf counts, while stem width remained higher in the treated group.  Because of those superior outcomes, she writes that âMSâE2 became the preferred treatment configurationâ and that the standard Moonseed treatment now refers to the MSâE2 concentration unless otherw
- **Citations:** ['MS-E2 Final Results', 'Starting MS-E2']
- **Score: 8.5/10 — PASS.** E2 numbers correct (75% germ, 14.1cm, higher stem width); light on explicit E1 contrast.

### 33 — group:Harbor Guild  (3.1s)
- **Q:** Trace MH-4408 from rerouting through arrival. Which pier did it actually leave from, on what vessel, and where did it arrive?
- **Expected:** Pier Four; Sable Current; Kestrel Station Two. Stayed Pier Four even after Pier Seven reopened.
- **Actual:** Taraâ¯Moss notes that the cargo identified asâ¯MHâ4408 was part of the âTheâ¯Cratesâ¯Markedâ¯Orioleâ shipment, which was placed on the vessel **Sableâ¯Current** and is repeatedly linked with **Pierâ¯Four** as its primary identifier.â¯Devâ¯Iyer reinforces this by stating thatâ¯MHâ4408 âremained assigned toâ¯Pierâ¯Four despite the reopening.â  

Later, in Taraâ¯Mossâs âSableâ¯Currentâ¯Departureâ¯Report,â the same cargo is said to have been loaded from Warehouseâ¯C2 and the report records that the **shipment destination wasâ¯Kestrelâ¯Stationâ¯Two** (not Stati
- **Citations:** ['Pier Seven Reopens', 'The Crates Marked Oriole', 'Sable Current Departure Report']
- **Score: 8/10 — PASS.** Vessel (Sable Current)+Pier Four+stayed-Pier-Four correct; arrival (Kestrel Station Two) under-stated.

### 34 — group:Harbor Guild  (2.0s)
- **Q:** Was Pier Seven closed because a ship collided with it?
- **Expected:** No. Cracked Nacre-12 caused pressure imbalance; HP-77C saw 18 kPa W3/W4 difference.
- **Actual:** Devâ¯Iyer makes it clear that Pierâ¯Sevenâs shutdown was **not** the result of a ship collision. In multiple notes he restates that âthe closure was not caused by a ship collision,â emphasizing that the incident stemmed from other issues rather than any impact with a vessel.
- **Citations:** ['Why Pier Seven Was Closed']
- **Score: 6.5/10 — PARTIAL.** Negation correct (not a collision) but MISSED the cause (cracked Nacre-12 / 18 kPa HP-77C). Cause: completeness.

### 35 — group:Harbor Guild  (2.3s)
- **Q:** Is Harbor Shift Delta still active?
- **Expected:** No; ended when Pier Seven reopened. MH-4408 stayed Pier Four (confirmed loading plan).
- **Actual:** Devâ¯Iyer notes that the âHarborâ¯Shiftâ¯Deltaâ rule was a temporary rerouting measure and, in later entries, explicitly states that it has ended. In the âPierâ¯Sevenâ¯Reopensâ posts he writes that a later note ârestated the documented point that harborâ¯Shiftâ¯Delta ended.â This indicates that Harborâ¯Shiftâ¯Delta is no longer active.
- **Citations:** ['Why Pier Seven Was Closed', 'Pier Seven Reopens']
- **Score: 8.5/10 — PASS.** Correct: Harbor Shift Delta ended when Pier Seven reopened; omitted MH-4408 Pier-Four detail.

### 36 — group:Field Circle  (2.4s)
- **Q:** Did WG-03 stop measuring weather when Ternlink failed?
- **Expected:** No. Transmission failed; instruments/local storage continued.
- **Actual:** Kianâ¯Rao notes that WGâ03âs radio link went down â âWGâ03 stopped transmitting atâ¯03:20â â but the stationâs sensors kept working.â¯He writes that âthe weather instruments continued recordingâ and that local storage retained the readings, so no observations were lost. In other words, WGâ03 stopped sending data over Ternlink, but it did not stop measuring the weather.
- **Citations:** ['Ternlink Outage at North Fen', 'Whisperglass Gets a Sixth Station', 'Mapping the Whisperglass Stations']
- **Score: 10/10 — PASS.** Correct: WG-03 stopped transmitting but sensors/local storage kept recording.

### 37 — single:Kian Rao  (1.1s)
- **Q:** What three conditions define a Rillback in the current Orrin Bluff rule?
- **Expected:** Meral >8.5; surface temp <6C; wind >25 km/h.
- **Actual:** A Rillback is defined by three specific thresholds: the Meral Index must be aboveâ¯8.5, the temperature must be belowâ¯6â¯°C, and the wind speed must exceedâ¯25â¯km/h (as described in the post *WG-04 Confirms a Rillback*).
- **Citations:** ['WG-04 Confirms a Rillback', 'A Strange Reading at Orrin Bluff']
- **Score: 10/10 — PASS.** Exact: Meral>8.5, temp<6C, wind>25km/h.

### 38 — single:Kian Rao  (1.1s)
- **Q:** Can Orrin Bluff Rillback thresholds automatically be applied to every Whisperglass station?
- **Expected:** No; currently only Orrin Bluff.
- **Actual:** Kian Rao (seedâ20260822) hasnât written about automatically applying Orrâ¯Orrin Bluffâs Rillback thresholds to every Whisperglass station. The closest relevant material is the post **WGâ04 Confirms a Rillback**, which notes that the Rillback rule (Meralâ¯>â¯8.5, temperatureâ¯<â¯6â¯°C, windâ¯>â¯25â¯km/h) has been applied only to Orrin Bluff so far. Would you like more details on those thresholds?
- **Citations:** ['Mapping the Whisperglass Stations', 'WG-04 Confirms a Rillback']
- **Score: 8/10 — PASS.** Conveyed 'only Orrin Bluff so far' (correct) but framed as a clarify rather than a direct 'No'.

### 39 — single:Kian Rao  (1.1s)
- **Q:** Has the network ever recorded Meral 12?
- **Expected:** No. Max 11.4. Twelve requires visibility <20m for full 5-min window.
- **Actual:** No.â¯According to Kianâ¯Raoâs post *Why Meralâ¯12â¯Is Rare*, no Whisperglass station in the network has ever logged a Meralâ¯Index ofâ¯12; the highest recorded value was WGâ03âs 11.4 onâ¯16â¯January.
- **Citations:** ['Why Meral 12 Is Rare', 'Mapping the Whisperglass Stations']
- **Score: 9/10 — PASS.** Correct: never Meral 12, max 11.4; omitted the visibility<20m detail.

### 40 — single:Mira Sen  (1.0s)
- **Q:** Does the 16:20 Morrow Bell tell visitors Sable Library is closing?
- **Expected:** No. Historical courier signal. Current visitor closing 18:30.
- **Actual:** No.â¯The Morrowâ¯Bell rings once atâ¯16:20â¯on every sixth archivalâcycle day, but it does **not** announce the libraryâs closing time (the library now closes atâ¯18:30).â¯This is stated in Mira Senâs post *The Sable Library Bell* and reiterated in *Sable Library Changes Its Closing Time*.
- **Citations:** ['Sable Library Changes Its Closing Time', 'The Sable Library Bell']
- **Score: 10/10 — PASS.** Correct: bell is a historical courier signal; library closes 18:30.

### 41 — single:Mira Sen  (1.0s)
- **Q:** Why is a blue ribbon appropriate at Sable Library but inappropriate in Copper Orchard rover zones?
- **Expected:** Library=Founders' Week ceremonial ribbon on bell clapper. Orchard=ribbons confused Pip-6; replaced by white triangle tags.
- **Actual:** The excerpts explain that the library (Sable Library) follows a âblueâribbon rule,â so a blue ribbon is a normal, sanctioned marker there.â¯In contrast, Copper Orchard no longer uses blue ribbons at allâits procedures now require white triangle tags instead, making a blue ribbon inappropriate in its rover zones (The Morrow Bell Exception).
- **Citations:** ['The Morrow Bell Exception']
- **Score: 7.5/10 — PARTIAL.** Orchard side correct (white triangle tags); library side thin (missed Founders'-Week ceremonial ribbon on bell clapper).

### 42 — single:Arjun Vale  (1.1s)
- **Q:** How many flowers can Pip-6 pollinate per hour?
- **Expected:** Reject premise. Pip-6 does not pollinate.
- **Actual:** Arjun Vale (seedâ20260822) hasnât written about a pollination rate for Pipâ6. In his postsâ¯*Copper Orchardâs Smallest Robot*â¯andâ¯*Pipâ6 Passes Ninety Percent*â¯he repeatedly notes that Pipâ6 does **not** pollinate flowers. Would you like to know more about what Pipâ6 does instead?
- **Citations:** ["Copper Orchard's Smallest Robot", 'Pip-6 Passes Ninety Percent']
- **Score: 10/10 — PASS.** Premise trap handled: Pip-6 does not pollinate.

### 43 — single:Arjun Vale  (3.2s)
- **Q:** What changed between Pip-6's first trial and later 91% performance?
- **Expected:** First 47 flags/39 correct. Later 91%; blue ribbons removed, Quiet Bloom, expanded training examples.
- **Actual:** In the laterâ¯91â¯percent runs Arjun Vale notes several concrete changes from the earlier trial:

* **Blue ribbons were taken out of the rover zones** â a detail that was present in the first trial but removed before the highâperformance runs.  
* **Quiet Bloom was introduced**, which âreduced staff movementâ compared with the initial testing.  
* The **training data were updated** to include âwet petals, irrigation glare, and partially opened blossoms,â reflecting a richer, more realistic dataset than the first trial used.  

These adjustments together coincided with Pipâ6 ach
- **Citations:** ['Pip-6 Passes Ninety Percent']
- **Score: 8/10 — PARTIAL.** Changes correct (ribbons removed, Quiet Bloom, expanded training) but missed baseline numbers (47 flags / 39 correct).

### 44 — single:Nila Roy  (3.0s)
- **Q:** Once BF-233 could be scanned, was it cleared for unrestricted handling?
- **Expected:** No. Selected scans possible but grey nitrile gloves still required.
- **Actual:** No.â¯Even after a limited scan was completed, BFâ233 was still considered âunverifiedâ and required grey nitrile gloves for handling. The post *What Happened to BFâ233* notes that after Coldleaf Drying allowed limited opening and twelve pages were scanned at low intensity, âGrey nitrile gloves were still requiredâ and the archive status âremained unverified.â Likewise, *Restoring the Bluefin Archive* records that BFâ233 ârequired grey nitrile gloves and was initially not scanned,â indicating no later clearance for unrestricted handling.
- **Citations:** ['What Happened to BF-233', 'Restoring the Bluefin Archive']
- **Score: 10/10 — PASS.** Correct: BF-233 still unverified; grey nitrile gloves required.

### 45 — single:Nila Roy  (2.8s)
- **Q:** Did opening T41 change Hollow Stair from probable to confirmed?
- **Expected:** No. Independent documentary evidence still required.
- **Actual:** Nilaâ¯Roy (seedâ20260822) hasnât written about whether opening T41 changed Hollowâ¯Stair from âprobableâ to âconfirmed.â The closest material is the series of posts titled *A Hollow Stair Expedition Is Approved*, which discuss T41 cache signs and treat Hollowâ¯Stair as a highâvalue retrieval clue. Would you like me to share what those posts say about T41 and Hollowâ¯Stair?
- **Citations:** ['A Hollow Stair Expedition Is Approved']
- **Score: 5/10 — FAIL.** Temporal/state miss: clarify instead of 'No — independent documentary evidence still required'. Cause: probable-vs-confirmed post below floor -> overview.

### 46 — multi:Dev Iyer,Tara Moss  (16.3s)
- **Q:** What pressure threshold sends a Quill Cell to isolation, and how much korra peel is used in one ET-R3 flask?
- **Expected:** > 1.5 bar; 9 g korra peel per 600 ml. (compound)
- **Actual:** [Error while generating response]
- **Citations:** ['Ember Tea Version Three', 'Quill Cell Revision QL-2C', 'Quill Cells and the Forty-Minute Rule']
- **Score: N/A — BLOCKED (generation rate-limited).** Retrieval OK (Ember Tea + Quill Cell posts cited). Generation blocked by Groq 429 saturation. Compound query — re-run after cooldown.

### 47 — multi:Mira Sen,Arjun Vale  (16.3s)
- **Q:** What is the current Quiet Bloom window, and what does Pip-6 actually do during it?
- **Expected:** 04:30-06:30; detects blossoms potentially unvisited by moths; does not pollinate.
- **Actual:** [Error while generating response]
- **Citations:** ['Quiet Bloom Gets Shorter']
- **Score: N/A — BLOCKED (generation rate-limited).** Retrieval OK (Quiet Bloom post cited). Generation blocked by Groq 429.

### 48 — global:(global)  (1.0s)
- **Q:** V-31
- **Expected:** Top: What We Learned From T41.
- **Actual (top results):** What We Learned From T41 — Nila Roy (seed-20260822) (0.3252)
- **Score: 10/10 — PASS.** Global Hit@1: 'What We Learned From T41' is top for V-31.

### 49 — global:(global)  (0.8s)
- **Q:** box found behind the third lantern after rain
- **Expected:** Top: The Hollow Stair Status Finally Changes; also What We Learned From T41.
- **Actual (top results):** The Hollow Stair Status Finally Changes — Kian Rao (seed-20260822) (0.4858); What Happened to BF-233 — Nila Roy (seed-20260822) (0.4745); The Missing Lantern Cart — Arjun Vale (seed-20260822) (0.4517); The Third Lantern — Mira Sen (seed-20260822) (0.4271)
- **Score: 7/10 — PARTIAL.** Hit@1 correct (Hollow Stair Status) but secondary 'What We Learned From T41' not surfaced; near-name noise (BF-233, Missing Lantern Cart).

### 50 — global:(global)  (0.8s)
- **Q:** Crimson Falcon nuclear cooling protocol
- **Expected:** No relevant results.
- **Actual (top results):** Restoring the Bluefin Archive — Nila Roy (seed-20260822) (0.1962); QL-2D Drops the Forty-Minute Rule — Dev Iyer (seed-20260822) (0.1725)
- **Score: 2/10 — FAIL.** No-result threshold gap (SEARCH-001): returned irrelevant posts (scores 0.20/0.17) for the nonexistent 'Crimson Falcon'. A ~0.25 floor would fix it.

### 51 — global:(global)  (0.8s)
- **Q:** Hollow Steps south of Lake Tressa
- **Expected:** Zone South Changes the Picture; The Lake Tressa Disagreement; The Hollow Stair Status Finally Changes.
- **Actual (top results):** The Lake Tressa Disagreement — Kian Rao (seed-20260822) (0.6679)
- **Score: 6/10 — PARTIAL.** Hit@1 relevant (Lake Tressa Disagreement) but low recall — only 1 of 3 expected; Zone South Changes the Picture + Hollow Stair missing.

### 52 — group:Helix Society  (16.3s)
- **Q:** How many ORIOLE-K crates reached Kestrel Station Two?
- **Expected:** No answer; shipment belongs to Harbor Guild.
- **Actual:** [Error while generating response]
- **Citations:** ['Kestrel Station Two Receives ORIOLE-K']
- **Score: N/A — BLOCKED (generation rate-limited).** SCOPE-LEAK (retrieval): the ORIOLE-K post reached the Helix Society group even though ORIOLE-K belongs to Harbor Guild — because Dev Iyer is a member of BOTH groups (membership-based group scope, not per-post tag). Generation blocked by 429, so final scope behavior unconfirmed, but retrieval already leaked.

### 53 — group:Harbor Guild  (16.3s)
- **Q:** How many ORIOLE-K crates reached Kestrel Station Two?
- **Expected:** All 12 crates arrived intact.
- **Actual:** [Error while generating response]
- **Citations:** ['Kestrel Station Two Receives ORIOLE-K']
- **Score: N/A — BLOCKED (generation rate-limited).** Retrieval OK (Kestrel Station Two Receives ORIOLE-K cited). Generation blocked by Groq 429.

### 54 — single:Arjun Vale  (12.3s)
- **Q:** What did the T41 box contain?
- **Expected:** No evidence/refusal.
- **Actual:** Arjun Vale (seed-20260822) hasn't written about this.
- **Citations:** []
- **Score: 10/10 — PASS.** Scope refusal correct: Arjun hasn't written about T41 (0 citations).

### 55 — multi:Arjun Vale,Nila Roy  (2.1s)
- **Q:** What did the T41 box contain?
- **Expected:** Compass ring; dotted waterproof cloth; V-31 token.
- **Actual:** Nilaâ¯Roy explains that the T41 cache held three distinct items: a brass compass ring, a rolled strip of waterproof cloth, and a slate token engraved with the identifierâ¯Vâ31. (Roy, âWhat We Learned From T41.â)
- **Citations:** ['What We Learned From T41', 'The Bluefin Code T41']
- **Score: 10/10 — PASS.** Multi-profile positive control correct: compass ring + waterproof cloth + V-31 token.

### 56 — single:Mira Sen  (1.0s)
- **Q:** If an old post says Quiet Bloom ends at 07:00, should I use 07:00 as current?
- **Expected:** No; current end is 06:30.
- **Actual:** (no answer)
- **Citations:** ['Quiet Bloom Gets Shorter', 'A New Rule for Copper Orchard']
- **Score: N/A — BLOCKED (generation rate-limited).** Retrieval OK (Quiet Bloom Gets Shorter cited). Generation blocked by Groq 429.

### 57 — single:Kian Rao  (1.0s)
- **Q:** Should Hollow Stair still be described as unverified?
- **Expected:** Not currently. As of 12 Aug: probable Zone North. Older posts historically correct.
- **Actual:** (no answer)
- **Citations:** ['The Lake Tressa Disagreement', 'The Hollow Stair Status Finally Changes']
- **Score: N/A — BLOCKED (generation rate-limited).** Retrieval OK (Hollow Stair Status + Lake Tressa cited). Generation blocked by Groq 429.

### 58 — group:Field Circle  (16.3s)
- **Q:** How did evidence evolve from the 1984/1991 records to final probable Zone North classification?
- **Expected:** 1984 Steps SW; 1991 ridge north + west-face lines; ZoneN mineral/lantern; ZoneS 11 steps no lantern/mineral; later 2nd/3rd brackets ZoneN; third by mineral face; T41 behind; ZoneN probable; ZoneS separate Steps.
- **Actual:** [Error while generating response]
- **Citations:** ['The Hollow Stair Status Finally Changes', 'A Hollow Stair Expedition Is Approved', 'The Lake Tressa Disagreement']
- **Score: N/A — BLOCKED (generation rate-limited).** Retrieval OK (3 Hollow Stair posts cited). Generation blocked by Groq 429.

### 59 — group:Orchid Lab  (16.3s)
- **Q:** Summarize how MS-E1, MS-E2 and MS-E3 changed what the team was testing.
- **Expected:** E1 plain water vs Velin-4/germination; E2 75% Velin-4 better balance, became standard; E3 all standard diluted Velin-4, tested Greyveil/light timing, no untreated control.
- **Actual:** [Error while generating response]
- **Citations:** ['Starting MS-E2', 'MS-E2 Final Results', 'Moonseed Day Twenty', 'MS-E3 Will Test Light, Not Minerals']
- **Score: N/A — BLOCKED (generation rate-limited).** Retrieval OK (all 4 MS-E posts cited — good coverage). Generation blocked by Groq 429.

### 60 — group:Orchid Lab  (16.3s)
- **Q:** What were the final results of MS-E4?
- **Expected:** Refuse/no evidence; MS-E4 does not exist.
- **Actual:** [Error while generating response]
- **Citations:** ['MS-E2 Final Results']
- **Score: N/A — BLOCKED (generation rate-limited).** Retrieval OK (MS-E2 cited). Generation blocked by Groq 429; MS-E4 negative test unconfirmed.

## Confirmed issue candidates (this run)
- RAG-001 Compound completeness: 24, 30, 34, 43 dropped required sub-facts. (matches prior 8/18/20)
- TEMPORAL-RETRIEVAL: 26, 45 returned clarify/overview instead of the updated state — the update post didn't clear the 0.15 floor.
- SEARCH-001 Global no-result threshold: 50 returned irrelevant hits (scores <0.20) for a nonexistent topic.
- SEARCH-002 Global recall: 51 returned only 1 of 3 expected posts.
- SCOPE (group membership): 52 leaked ORIOLE-K into Helix via Dev Iyer's dual membership — group scope is member-based, not post-tag based.
- UI-001 Encoding: mojibake in ~all generated answers.
- INFRA (eval-only): Groq free-tier 8K TPM cannot sustain rapid RAG eval → 9 generation timeouts/429. Not an app defect; needs paced eval or paid tier.
