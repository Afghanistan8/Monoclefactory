# Resolution logic

A step-by-step walkthrough of `adjudicate()`, `challenge()`, `resolve_challenge()` and `finalize()`
in `contracts/Monocle.py`, with the reasoning behind each design choice.

## 1. `adjudicate()`: preconditions

```python
if self.status == STATUS_CLOSED: raise UserError("Monocle is closed.")
round_str = str(int(self.current_round))
if self.round_status[round_str] != ROUND_OPEN: raise UserError("Current round is not open for adjudication.")
if not ids: raise UserError("No interpretations submitted this round.")
self.round_status[round_str] = ROUND_ADJUDICATING
```

* `ROUND_ADJUDICATING` is a reentrancy guard. A second call fails the `ROUND_OPEN` check. If
  anything later reverts (for example `LLM_MALFORMED`), GenVM rolls the whole transaction back,
  guard included (`test_malformed_llm_output_reverts_and_never_strands_adjudicating`,
  `test_second_adjudicate_while_adjudicating_is_rejected`).
* Adjudication is also allowed while the Monocle is `closing`. Backers keep the right
  to force a verdict during the close timelock.

## 2. Locals only

The closure gets `source_list`, `candidates`, `title`, `interpretation_type`, `schema`,
`prior_hash`, `has_live` and `nothing_new`. `candidates` comes from `_candidates()`. It carries
`id`, `stance`, `content`, `claims` and `fields`, and **never** `total_stake`, `backers` or
`author` (`test_prompt_never_contains_stake_backers_or_authors`).

The author is withheld too, and reputation never enters.

## 3. The single nondet block

The code calls `gl.vm.run_nondet(leader_fn, validator_fn)`: v0.6's custom-validator
primitive, with no extra sandbox. It is the one and only nondet call
in the method, and every `gl.nondet.*` call is reachable only from inside it (`genvm-lint` clean).

`leader_fn(force_judge=False)` runs these stages in order:

| Stage | Code | Notes |
| --- | --- | --- |
| 1. Fetch | `_fetch_sources` | `gl.nondet.web.render(url, mode="text", wait_after_loaded="3s")` for every source. An exception or empty page gives `ok=False` and an empty excerpt |
| 2. Sanitize / hash | `_sanitize_input`, `_text_fingerprint` | The excerpt is the sanitized text, capped at 2000 characters. `content_hash` is SHA-256 of the **raw** text with whitespace collapsed, capped at 20 000 characters |
| 3. Snapshot | `_evidence_snapshot` | Sliced from the real excerpts (400 characters each, 12 max). The model is never asked for one |
| 4. Evidence hash | `_evidence_hash` | SHA-256 of the sorted `(url, content_hash)` pairs that fetched. Order-independent |
| 5. No evidence | early return `no_evidence` | **No LLM call** (`test_fails_closed_without_llm_call_when_all_sources_unfetchable`) |
| 6. Unchanged | early return `unchanged` | Only if a live output exists, **every** candidate's content hash was already judged in the round that produced it, and the evidence hash matches. No LLM call |
| 7. Prompt | `_build_adjudication_prompt` | Fenced `INTERPRETATION_TYPE`, `SCHEMA`, `INTERPRETATIONS` and `LIVE_EVIDENCE` blocks, each opening with an `UNTRUSTED CONTENT` label Four stages (extract, then score claims, then score interpretations, then rank) inside one prompt |
| 8. Model | `gl.nondet.exec_prompt(prompt)` | **No `response_format="json"`**, because GenVM auto-parses at the call boundary and bare floats crash calldata. Parsed by `_parse_json_object` |
| 9. Malformed | `raise UserError("LLM_MALFORMED: …")` | An LLM error. The validator disagrees, which forces leader rotation |
| 10. Verdict | `_score_verdict` | Deterministic validation and roll-up (next section) |

### `_score_verdict`: deterministic gates, identical for leader and validators

1. The `winner_id` must be a submitted id. An unknown id is never coerced to a valid
   candidate: the result is `invalid_verdict` and the round **fails closed**
   (`test_invalid_winner_id_fails_closed_instead_of_crowning_first_candidate`).
2. A claim table covers **every** declared claim of every candidate and defaults to
   `insufficient`. Model rows are matched on `(interpretation_id, claim_id)`. Unknown rows are
   ignored.
3. `support_source_urls` is normalized and filtered to URLs that **actually fetched this pass**.
   A `supported` verdict with no remaining URL is downgraded to `insufficient`
   (`test_uncited_supported_claim_is_downgraded`,
   `test_citing_a_source_that_failed_to_fetch_does_not_corroborate`).
4. The roll-up for each candidate is `rollup_bps = max(0, (supported − contradicted) · 10000 // claims)`.
   It also records `corroborating_sources`, the distinct cited URLs.
   `disqualified = core claim contradicted AND stance != "refutation"`.
5. The ranking, if present, may be a subset (omitted ids count as DQ'd). Unknown or duplicate
   ids return `invalid_verdict`. The winner must hold the top score, and `composite_score` is
   taken from the winner's ranking entry.
6. A disqualified winner returns `invalid_verdict`.
7. If another non-disqualified candidate has a strictly higher `rollup_bps`, the result is
   `invalid_verdict` ("inconsistent with the claim-level scores"). This is how "prefer more
   corroborated claims, not more words" is enforced mechanically.
8. If the winner has 0 supported claims or fewer than `CORROBORATION_MIN_SOURCES = 2`
   corroborating fetched sources, the result is `insufficient_corroboration`.
9. Otherwise the result is `decided`.

The payload is calldata-safe: strings, ints, bools, lists and dicts only. Every number the model
produced is a clamped decimal string (`_stringify_confidence`: 4 decimal places, NaN and bool
become `"0.0"`).

## 4. `validator_fn`

```python
if not isinstance(leader_result, gl.vm.Return):
    if msg.startswith("LLM_MALFORMED"): return False     # LLM error -> rotate
    try: leader_fn(); return False                      # I succeeded, leader failed -> disagree
    except UserError as e: return e.data == msg         # same deterministic error -> agree
    except Exception: return False                      # transient -> disagree
theirs = leader_result.calldata
mine = leader_fn() if theirs.decision in (no_evidence, unchanged) else leader_fn(True)
return _verdicts_agree(mine, theirs)
```

This follows GenLayer's error-classification pattern: deterministic errors must match, transient
errors disagree, LLM errors rotate. The validator **re-fetches and re-reasons**. It never checks
the shape of the leader's JSON alone (`test_validator_independently_refetches_evidence`,
`test_validator_independently_rereasons`).

For a decided leader, the validator calls `leader_fn(True)`, which skips the unchanged short-cut.
Otherwise a validator whose own fetch happens to equal the prior snapshot would disagree with a
legitimate re-judgment
(`test_validator_rejudges_a_decided_leader_even_if_its_own_fetch_matches_prior`).

`_verdicts_agree` requires the same `decision`. For `decided` it also requires:

* the same `winner_id`;
* `|Δconfidence| < 0.15`;
* `|Δcomposite_score| < 0.12`.

Reasoning text is never compared. For every non-decided decision, agreement on the decision
itself is enough, and all of them fail closed.

## 5. Post-consensus effects (deterministic, after `run_nondet` returns)

1. Build the adjudication record: decision, reason, confidence, composite, reasoning, snapshot,
   evidence hash, claim scores, roll-ups, ranking, fetch report, sources checked, count fetched OK.
2. `_apply_fetch_report`: per-source `last_fetch_ok`, `last_content_hash`, `misses` and
   `fetch_ok_count`. A `locked` source bond becomes `refundable` on a fetch that succeeds, and
   `forfeited` (moved to `carry`) after 3 misses **while another source fetched**.
3. Then, by decision:
   * `unchanged`: the round becomes `unchanged` (refundable). The live output is untouched and
     the next round opens.
   * not `decided`, or confidence < **0.62**: the round becomes `inconclusive` (refundable). The
     live output is untouched and the next round opens. The threshold is deliberately above 0.5.
   * `decided`: the round becomes **`decided_pending`**. The pending winner is stored and
     `challenge_deadline = now + 3600`. **The live output is NOT updated** and **no new round
     opens yet**. Publication waits for the challenge window.

## 6. `challenge(round, alternative_id)` (payable)

* The round must be `decided_pending`, and `now <= challenge_deadline`.
* `value >= min_challenge_bond`.
* The alternative must be in the same round and must differ from the pending winner.
* Only one challenge per round is allowed (the status moves to `challenged`).
* The bond is credited to `round:<n>`.

## 7. `resolve_challenge(round)`

* Anyone may call it. The status moves to `resolving` first, as a guard.
* One nondet block runs a pairwise comparison of the original and the alternative against a
  fresh fetch. The prompt is fenced and stake-blind
  (`test_arbiter_prompt_is_stake_blind_and_fenced`).
* The validator re-runs it independently: same decision, same `preferred_id`, and confidence
  within 0.15.
* Outcomes:
  * `upheld` (the alternative is preferred with confidence ≥ 0.62): the pending winner becomes
    the alternative.
  * `rejected` (the original is preferred with confidence ≥ 0.62): the bond joins the pot.
  * `unresolved` (no evidence, an invalid pick, or low confidence): the bond is refunded and the
    original stands.
* Then `_finalize_round`.

## 8. `finalize(round)`

* `decided_pending` and `now > challenge_deadline` → `_finalize_round`.
* `challenged` or `resolving` and `now >= challenged_at + 24h` → outcome `expired` (bond
  refunded, original stands) → `_finalize_round`.
* Anything else reverts.

`_finalize_round`:

1. Attaches the `carry` bucket to this round's pot.
2. Computes `pot` and `challenger_payout` (see
   [ARCHITECTURE.md](ARCHITECTURE.md#economic-flows)).
3. Sets `live_interpretation_id`, `live_round`, `live_since`, `prior_evidence_hash` and
   `prior_judged_hashes`.
4. Records local reputation.
5. Opens the next round, unless the Monocle is closed.

## 9. `settle(round)` / `claim(round)`

* `settle` is allowed only on `finalized`, never directly after a verdict.
* `claim` works on `settled`, `inconclusive`, `cancelled` and `unchanged`. It allows one claim
  per `(round, address)`, marks the claim before paying, and pays through `MonocleVault.pay`
  (`emit_transfer`, `on='finalized'`).
* Settled payout is `winner_stake · pot // winner_total`, except that the last winning claimant
  receives `pot − paid_winners`. The challenger additionally receives `challenger_payout`.
  Losers can claim `0`, which marks them as resolved.

## 10. Close

* `close_monocle()` (creator only) moves the Monocle to `closing` and records
  `close_requested_at`. Nothing is cancelled yet.
* `cancel_close()` (creator only) returns it to `active`.
* `finalize_close()` is permissionless, available after 7200 s, and only while the current round
  is `open` or `adjudicating`. It:
  1. cancels that round;
  2. makes locked source bonds refundable;
  3. moves `carry` to `residual`;
  4. sets the status to `closed`.
* `flush_residual()` (permissionless, `closed` only) sends the residual to
  `factory.receive_residual()` for factory-made Monocles, or to the deployer otherwise.
