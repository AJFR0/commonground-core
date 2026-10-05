# Anchor-selection playbook and the teaching ("illumination") floor

Ported from the public Commonground repo's design notes (June 2026). Shared by both apps.

## The problem

Correctness isn't teaching. A card can cite real docs, never overstate a capability, and still teach nothing, because the anchor only **renames** the concept's parts in its own vocabulary.

An illumination sweep of the 19 concepts shipped at the time (Vector Search x 12, Unity Catalog x 7) scored pack averages of 3.5 and 3.0 out of 5.

**What the sweep revealed:** a concept teaches well through an analogy when it has *internal structure* the anchor can mirror:

- a **hierarchy**: namespace → pantry section → shelf → jar
- a **trade-off dial**: in-memory vs. on-disk → mise-en-place vs. walk-in fridge
- a **duality**: meaning + exact term → eye test + box score

Concepts that are a **single relationship** ("a log", "a thing has an owner", "one rulebook covers everything") have nothing to mirror. Every anchor collapses into relabeling, and the only real teaching survives in "where it breaks".

**Anchor data (guidance, not law):**

- **Cooking** is the workhorse: concrete physical structure plus universal familiarity.
- **Hockey** wins on idiom.
- **F1** is high-variance: great when there's a real-time or stakes element, weak on static ideas.
- **Gardening** mostly relabels.
- Governance concepts land best in institutional domains: hospital records, bank and ledger, library archive, building security.

## Part 1: choosing anchors for a new topic

1. **Profile each concept by shape**: hierarchy, trade-off, pipeline, matching, permission, record, lifecycle, or single relationship (`commonground_core.spine.SHAPES`). Single-relationship concepts are the hard ones.
2. **Find domains that natively contain those shapes.**
   - Access, records and ownership → regulated or institutional domains.
   - Similarity and tuning → evaluative or competitive domains: scouting, tasting, racing telemetry.
3. **Keep a small universal core plus topic picks.** Cooking keeps a permanent seat; add 2–3 domains chosen in step 2.
4. **Pilot cheaply.** Craft one card for the 2–3 hardest concepts across 5–6 candidate domains, and judge them before authoring everything.
5. **Select for coverage, then lead with the best.** Every concept needs at least one strong anchor. Not every anchor has to explain every concept.

## Part 2: the floor

Rubric, scoring **pedagogical lift only** (correctness is a separate gate):

| Score | Meaning |
| --- | --- |
| 5 | Illuminating: the anchor's structure reveals a non-obvious insight |
| 4 | Strong: real structural fit |
| 3 | Adequate / relabeling: tidy, mostly renames parts |
| 2 | Weak: forced or generic |
| 1 | No fit: obscures more than it clarifies |

Watch for the **collapse smell**: if every anchor for a concept maps the same generic way, they're renaming, and the score caps near 3.

**Policy** (`commonground_core.teaching`):

- A concept ships with analogies only if its best anchor scores **≥ 4** (`ILLUMINATION_FLOOR`).
- Cards scoring **≤ 2** (`ILLUMINATION_CUT`) are cut candidates.
- The highest scorer is the concept's **lead** anchor.
- In a live app, `shown_analogies()` applies the conservative rule:
  - unscored analogies are never shown to readers;
  - weak ones are hidden;
  - with no analogy at 4+, the reader gets a plain explanation.

## Known backlog (public site)

Below the floor at the time of the sweep:

- Unity Catalog: `audit-log` (best 2), `metastore`, `securables-ownership`, `unity-catalog` (best 3 each).
- Vector Search pack: the `unity-catalog` concept (best 3).
