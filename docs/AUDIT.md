# Audit: self-adversarial pass

This review was done against the code actually shipped in `contracts/`. It covers the
well-known failure classes of GenLayer Intelligent Contracts that move capital, plus the attack
surface of MONOCLE's own features. Each finding names:

* the mechanism that closes it;
* the regression test in `tests/direct/` that proves it.

Status of the pass:

* `genvm-lint check` is clean on all three contracts.
* `gltest tests/direct`: **212 passed**.
* SDK unit tests: **13 TypeScript, 5 Python**, all passing.

## A. Known GenLayer failure classes

### A1. Success reported on a reverted transaction (critical)
A `gl.vm.UserError` revert still reaches ACCEPTED/FINALIZED, with `FINISHED_WITH_ERROR`.
**Mechanism:** `describeTransactionOutcome` (TypeScript) and `describe_transaction_outcome`
(Python) count a transaction as a success only when the status is ACCEPTED or FINALIZED **and**
the execution result is `FINISHED_WITH_RETURN`. They also require FINALIZED by default, and they
cross-check the SDK's own `isSuccessful`. A missing, `NOT_VOTED` or `NONDET_DISAGREE` result is
never treated as success. The fix lives in the SDK, so a future UI cannot regress it.
**Tests:**
* TypeScript: `ACCEPTED + FINISHED_WITH_ERROR is a failure`, `FINALIZED + FINISHED_WITH_ERROR is still a failure`, `missing or NOT_VOTED …`, `waitFinalized throws …`
* Python: `test_reverted_is_failure_even_when_accepted_or_finalized`, `test_missing_result_is_never_success`

### A2. Fail-open on zero evidence (critical)
**Mechanism:** with no fetchable source, the result is `no_evidence` before any LLM call. The
round goes inconclusive and pays full refunds.
**Tests:** `test_fails_closed_without_llm_call_when_all_sources_unfetchable`, `test_validator_agrees_on_matching_no_evidence_outcome`

### A3. Low-confidence decisions moving capital (critical)
**Mechanism:** `CONFIDENCE_THRESHOLD = 0.62`. On top of that, no capital moves
until `finalize`, and `settle` is only allowed on `finalized`.
**Tests:** `test_fails_closed_when_confidence_below_threshold`, `test_threshold_is_inclusive_at_0_62`, `test_settle_requires_finalized_round_not_merely_pending`

### A4. Stranded stake (critical)
Every round ends in a claimable status or reverts. The paths are:

* an open round that times out → `cancel_round`, refunds, **and a successor round opens**;
* a stuck `adjudicating` round → cancellable after the timeout;
* a stuck challenge → expires after 24h;
* close → cancels the open round;
* source bonds → refundable, or forfeited into the next pot, or refundable at close;
* forfeits left at close → residual → factory;
* rounding dust → the last winning claimant.

**Tests:**
* `test_cancel_round_after_timeout_unlocks_refund_and_opens_next_round`
* `test_stuck_adjudicating_round_is_still_cancellable_after_timeout`
* `test_unresolvable_challenge_expires_and_original_finalizes`
* `test_close_after_timelock_cancels_open_round_and_refunds`
* `test_close_makes_locked_source_bonds_refundable`
* `test_residual_after_close_flushes_to_factory_receive_residual`
* `test_rounding_dust_goes_to_last_winning_claimant_never_strands`
* `test_total_paid_equals_total_deposited_after_everyone_claims`

**Design point:** `cancel_round` always opens a successor round. Leaving `current_round` on a
cancelled round would leave the engine unable to accept submissions ever again. Covered by the
first test above.

### A5. Missing fee recovery / overpayment (critical)
**Mechanism:** `collected_fees += creation_stake` exactly, and the excess is refunded to the
creator with effects before interaction. `withdraw_fees` is owner-only and pays collected fees
plus residual.
**Tests:** `test_collected_fees_count_exactly_creation_stake_and_excess_is_refunded`, `test_withdraw_fees_only_owner_rejects_empty_and_pays_exact_sum`, `test_receive_residual_is_withdrawable_and_rejects_zero`, `test_factory_deploys_real_children_with_real_creator` (glsim: the refund message is really emitted)

### A6. LLM self-reported evidence (serious)
**Mechanism:** the snapshot is sliced from fetched text in contract code, and the model is never
asked for one. The evidence hash is computed in contract code too.
**Tests:** `test_evidence_snapshot_is_bound_to_real_fetched_content_not_llm_self_report`

### A7. A single self-controlled source (serious)
**Mechanism:** `MIN_SOURCES = 2`, enforced in the factory **and** the Monocle.
`add_source` is permissionless and bonded, and there is no remove method. In addition, the winner's
supported claims must cite ≥2 distinct sources that fetched.
**Tests:** `test_requires_at_least_two_sources`, `test_there_is_no_remove_source_method`, `test_single_cited_source_is_insufficient_corroboration`, `test_citing_a_source_that_failed_to_fetch_does_not_corroborate`

### A8. Validator checks shape only (serious)
**Mechanism:** the validator calls `leader_fn` itself, re-fetching and re-reasoning, and compares
decision, winner, confidence and composite.
**Tests:** `test_validator_independently_refetches_evidence`, `test_validator_independently_rereasons`, `test_validator_disagrees_on_*`

### A9. Float calldata crash (serious)
**Mechanism:** there is no `response_format="json"`. Parsing is defensive, and every number is a
clamped decimal string. Floats in user claims become strings.
**Tests:** `test_confidence_bare_float_never_crashes_and_is_stored_as_string`, `test_submit_deep_sanitizes_float_claims`, `test_stringify_confidence_is_always_a_clamped_decimal_string`

### A10. Address checksum mismatch (serious)
**Mechanism:** every key and comparison goes through `_normalize_address`, which lowercases.
**Tests:** `test_address_normalization.py` (6 tests: backing, claimable, reputation, creator, helper, factory filters)

### A11. Close-to-grief (medium)
**Mechanism:** close runs on a 2h timelock:
* it cannot cancel an open round instantly;
* backers can adjudicate during the timelock;
* it waits for a pending verdict to finish;
* the creator can cancel the close.

**Tests:** `test_close_starts_timelock_and_cannot_instantly_cancel_open_round`, `test_backers_can_adjudicate_and_settle_during_close_timelock`, `test_finalize_close_waits_for_pending_round`, `test_cancel_close_restores_active`

### A12. Invalid winner crowned as candidate[0]
**Mechanism:** an unknown `winner_id` gives `invalid_verdict`, and the round goes inconclusive.
**Tests:** `test_invalid_winner_id_fails_closed_instead_of_crowning_first_candidate`

### A13. The factory as the creator of every child
Inside a factory-deployed contract, `gl.message.sender_address` is the **factory**. Storing it as
the creator would mean `close_monocle` could never be called by the person who opened the
Monocle.
**Mechanism:** the factory passes the real creator explicitly.
**Tests:** `test_explicit_creator_overrides_deployer`, `test_create_registers_metadata_and_deploys_child_with_real_creator`, `test_factory_deploys_real_children_with_real_creator` (glsim)

## B. Attack surface of MONOCLE's own features

### B1. Challenge games
* **Delay-by-challenge.** A challenge needs a bond ≥ `min_challenge_bond`, and anyone can call
  `resolve_challenge` immediately. A challenge that cannot be resolved expires after 24h: the
  original verdict stands and the bond is refunded. The worst case is a 24h delay to FINAL, paid
  for with locked capital.
* **Farming the reward.** The reward is `min(bond, 10% of the stake pool)` and is paid only when
  the challenge is upheld with confidence ≥ 0.62.
* **Challenging with a garbage alternative.** A confident rejection forfeits the bond to the
  winners.
* **Double challenge.** Only one challenge per round is allowed.

**Tests:** `test_only_one_challenge_per_round`, `test_challenger_reward_is_capped_at_bond`, `test_failed_challenge_forfeits_bond_to_winning_backers`, `test_unresolvable_challenge_expires_and_original_finalizes`, `test_challenge_after_window_is_rejected`
**Residual:** a challenge compares only two interpretations. It cannot surface a third, better
one (see [LIMITATIONS.md](LIMITATIONS.md)).

### B2. Hash collisions
* SHA-256 over canonical JSON; the FNV fallback is used only if `hashlib` is absent.
* Evidence hashes are used for equality ("unchanged"). A collision could at worst cause an
  `unchanged` refund round. It cannot crown anything or move funds.
* Content-hash dedup could at worst reject a genuinely different submission as a duplicate.

**Tests:** `test_stable_hash_is_deterministic_and_order_independent`, `test_fnv_fallback_is_deterministic_and_256_bit`

### B3. Prompt injection via claims JSON and web content
Structural fences come first:
* four tagged blocks, each opening with an UNTRUSTED CONTENT label;
* fence tags occur exactly once each;
* the preamble names the blocks without angle brackets.

Sanitization comes second: braces, backtick fences and control characters are stripped, and
known phrases **and fake fence tags** are replaced with `[FILTERED]`. All of this applies to
content, claims, fields, schema and fetched pages.

**Tests:** `test_prompt_injection_via_claims_json_cannot_break_out_of_fences`, `test_hostile_web_content_is_fenced_and_scrubbed`, `test_prompt_fences_untrusted_data_blocks`, `test_sanitizer_neutralizes_fence_tag_spoofing`

**Found and fixed during this audit:** the first draft of the prompt preamble named the tags as
`<LIVE_EVIDENCE>` etc., so every tag appeared twice in the prompt. That made "the first
`</INTERPRETATIONS>`" ambiguous. `test_prompt_fences_untrusted_data_blocks` caught it.

### B4. Vault conservation
The in-process `MonocleVault` keeps `sum(buckets) == tracked`, never debits past a bucket, and
updates the ledger before emitting any transfer. Round buckets are isolated.
**Tests:** `test_vault.py` (11 tests), `test_claimed_amounts_never_exceed_round_pool`, `test_challenge_bond_is_isolated_per_round`

### B5. Unchanged-hash false negatives from sanitizer changes
If the hash were computed over the sanitized excerpt, a change hidden by sanitization would be
missed. Examples: text inside braces, or text past the 2000-character excerpt.
**Mechanism:** the fingerprint covers **raw** fetched text with whitespace collapsed (up to
20 000 characters). `unchanged` also requires that no new interpretation was submitted.
**Tests:** `test_evidence_hash_detects_change_hidden_by_sanitizer`, `test_evidence_hash_detects_change_beyond_excerpt_truncation`, `test_new_interpretation_with_unchanged_evidence_is_rejudged`

### B6. Leader/validator split on "unchanged"
A validator whose fetch matched the prior snapshot could disagree with a leader that chose to
re-judge.
**Mechanism:** for a decided leader, the validator calls `leader_fn(True)`, which forces a
re-judgment.
**Tests:** `test_validator_rejudges_a_decided_leader_even_if_its_own_fetch_matches_prior`, `test_validator_disagrees_with_unchanged_leader_when_its_fetch_differs`

### B7. Source-bond griefing
* A transient outage never counts as a miss: a miss is recorded only when another source fetched
  in the same pass.
* A dead source forfeits after 3 misses, and the forfeit goes to future winners rather than to
  anyone who controls timing.

**Tests:** `test_global_outage_never_burns_a_source_bond`, `test_unfetchable_source_forfeits_bond_after_repeated_misses_into_next_pot`

### B8. Slot squatting (accepted, disclosed)
12 interpretation slots per round, each with a minimum bond. A funded attacker can fill a round
with junk and lock honest submitters out of that round.

**Mitigations:**
* junk loses its bonds to the winner, if any honest entry is among the 12;
* the round cycles on decision or on a 24h timeout;
* backing an existing entry is always possible.

**Operator lever:** raise `min_interpretation_bond` in the factory config.
This is not fully closed. The cap exists to bound prompt size and gas.

### B9. Leader-reported per-source fetch status (accepted, disclosed)
Validators agree on the verdict, not on each source's `ok` flag. A malicious leader could flip one
flag and release or forfeit one source bond early. The bounded value and the leader requirement
make this acceptable for v1. The fix would be to require an identical fetch set, which trades
this risk for liveness, because a flapping source would block consensus.

### B10. Close timelock vs pending rounds
`finalize_close` refuses while a verdict is pending. No new capital can enter while closing, so
after at most one finalize the next round is empty and close completes. A challenger can delay
close by at most 24h. The status is `closing`, and `cancel_close` exists.

## C. Rechecked and correct
* **Exactly one nondet block** per write method that needs one (`adjudicate`,
  `resolve_challenge`), with every `gl.nondet.*` reachable only from inside it. `genvm-lint`
  confirms this.
* **No `self.*` inside closures.** Everything is copied into locals first.
* **Every storage write happens after `run_nondet` returns**, except the adjudicating/resolving
  guard, which a revert rolls back.
* **Stake, backers, authors and reputation never reach a prompt**
  (`test_prompt_never_contains_stake_backers_or_authors`, `test_arbiter_prompt_is_stake_blind_and_fenced`,
  `test_reputation_is_never_an_adjudication_input`).
* **Factory metadata is creation-time only**, with no live mirror
  (`test_factory_metadata_is_creation_time_only`).
* **Owner privilege** covers only fee withdrawal and the future creation stake. There is no pause
  (`test_factory_has_no_pause_or_admin_escape_hatches`).
* **Timestamps** come only from `gl.message.raw["datetime"]`.

## D. What this audit could not do
* **A live Studio Next run.** The integration suite (`tests/integration`) is ready and skips
  without a node. Running it needs a funded account, and the post-window path takes about an
  hour.
* **Measuring model agreement rates** on real pages. Tolerances (±0.15 confidence, ±0.12
  composite) are chosen conservatively, not calibrated on v0.6 validators.
