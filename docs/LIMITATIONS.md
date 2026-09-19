# Limitations

This file lists what MONOCLE does **not** make mechanical or safe, so nobody has to discover it
the hard way.

## Still an LLM judgment

* **Claim verdicts come from a model.** Whether an excerpt "supports" or "contradicts" a claim
  is a language model's reading. MONOCLE constrains the reading:
  * support must cite a source that actually fetched;
  * uncited support is downgraded;
  * the pick must agree with the deterministic roll-up;
  * a contradicted core claim disqualifies;
  * a winner needs ≥2 corroborating sources, confidence ≥ 0.62, and agreement from independent
    validators.

  The reading itself stays qualitative. Two honest models can disagree, and the result is then a
  failed consensus or an inconclusive round, not a wrong crowning. A *consistently* wrong model
  shared by every validator can still crown a wrong answer. The challenge window only helps if
  someone notices and the arbiter model disagrees.
* **Claim extraction is only partly mechanical.** Submitters write their own claims, and "c0 of A
  is comparable with c0 of B" is left to the model. Nothing verifies that a claim is a faithful
  summary of its interpretation's prose.
* **Composite and confidence are self-reported.** They are clamped, compared across validators
  (±0.12 / ±0.15) and thresholded, but they are the model's own numbers.
* **Interpretation-type semantics are prompt-level.** `stance: "refutation"` changes a
  deterministic gate. Everything else about `interpretation_type` and `schema` is context the
  model reads.

## Evidence

* **The excerpt is not the whole page.** The model sees at most 2000 sanitized characters per
  source. The change-detection hash covers up to 20 000 raw characters, so changes past that
  point are invisible to both.
* **Whitespace-only changes count as unchanged**, by design (`test_whitespace_only_change_is_treated_as_unchanged`).
* **Pages that change on every load** (timestamps, ads) almost never produce `unchanged`. They
  also make leader/validator fetches differ. Validators do not require identical fetch sets, but a
  decisive difference in content causes disagreement and rotation.
* **Per-source fetch status is the leader's observation.** Validators agree on the verdict, not
  on each source's `ok` flag. A leader could misreport one source's fetch to release or forfeit a
  source bond. The value at risk is one source bond, and the attack requires being leader. This is
  accepted and listed in [AUDIT.md](AUDIT.md).
* **`unchanged` blocks re-judging identical submissions.** If evidence and interpretations both
  match the last FINAL round, the model is not asked again, even if the model's opinion would
  have changed. Any genuinely new interpretation or evidence change triggers a full re-judgment.

## Economics

* **Slot squatting.** 12 interpretations per round, each bonded. A well-funded actor can fill a
  round with minimum-bond submissions and lock others out *of that round*. Their bonds go to the
  winners if one of their entries loses, and the round cycles. Sizing `min_interpretation_bond`
  is the defence. See [AUDIT.md](AUDIT.md).
* **Reputation is sybil-able.** It records outcomes per address. An actor can author both sides.
  Treat it as a weak signal, never as identity.
* **The challenge reward is fixed** at 10% of the stake pool, capped at the bond. It is not
  tuned per market.
* **Rounding.** Pro-rata payouts floor, and the last winning claimant absorbs the remainder. That
  is deterministic but order-dependent at the wei level.

## Challenges vs appeals

MONOCLE's challenge is an **in-contract** dispute:

* it is bonded;
* it compares two interpretations pairwise;
* it runs in one new nondet block;
* it resolves within the contract's own rules.

It is **not** a GenLayer protocol appeal (`client.appealTransaction`), which re-runs the *same
transaction* with a larger validator set. The two are complementary:

* **Protocol appeal:** "these validators got this transaction wrong." It works before
  consensus finality and on any transaction.
* **MONOCLE challenge:** "this verdict should lose to this specific alternative." It works after
  the adjudication transaction is done, and before the verdict becomes FINAL live output.

A pairwise challenge does not re-run the full claim-level roll-up across all candidates. It only
asks which of two fits better. After an upheld challenge, the published claim scores and
roll-ups are still those of the original adjudication. They are labelled with
`challenge_outcome` and `final_winner_id`.

## Cross-contract

* **No shared vault.** Custody is per-Monocle. See the vault decision in
  [ARCHITECTURE.md](ARCHITECTURE.md#vault-decision).
* **Reputation is pull-based.** Rounds are not in the global aggregator until someone calls
  `record_round`.
* **`flush_residual` and `withdraw_fees` are messages.** They execute after the transaction
  finalizes, not inline.
* **Directly deployed Monocles** (not made by the factory) send their residual to the deployer
  and cannot be recorded by `MonocleReputation`.

## Studio Next itself

* **It is an RC environment.** State can be reset and availability is not guaranteed
  ([STUDIO_NEXT.md](STUDIO_NEXT.md#resets)). Treat every address as ephemeral.
* **The RC SDKs and tooling change.** Several harness gaps are documented in
  [STUDIO_NEXT.md](STUDIO_NEXT.md#runner-pin).
* **Live runs.** This contract system is an exact build of the one whose full lifecycle was run on
  Studio Next (create, two submissions, a real multi-validator adjudication of two live Wikipedia
  pages with the correct winner at 99% confidence, finalize after a 3-minute window, settle,
  claim). This repository's own deployment has not been exercised until you run
  `npm run deploy:studio-next` and `npm run seed:studio-next`; the seed script records every
  transaction hash in `deploy/deployments.json`. The challenge path (challenge, resolve) and the
  close path are exercised only in direct-mode and in-process multi-contract tests, not live.
