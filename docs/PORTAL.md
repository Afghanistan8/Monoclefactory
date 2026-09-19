# Portal guide

One page for attaching this repository and operating the MONOCLE contracts.

## What to attach

This repository. The contract system is the three files in `contracts/`, and `MonocleFactory` is
the entry point. Deploy it with the repo's deploy script: the factory embeds the Monocle and
MonocleReputation source as constructor arguments, which are far too large to paste by hand.

## Deploy order

1. **MonocleFactory** via `npm run deploy:studio-next`. Its constructor deploys **MonocleReputation**
   itself, as an internal message that lands shortly after the factory finalizes.
2. Check it with `npm run check:studio-next`.
3. Optional: seed a demo market with `npm run seed:studio-next`.

The addresses are whatever `deploy/deployments.json` records after **you** deploy.

Current deployment: factory `0x1FD888328B4Ffb3b31eD4952349EaF9413628671`, reputation
`0x1a9e8F610B85a2E40399b66AE5921BCf911cC969`, demo market `0x5dFF8925a8539b3fE7B227426b334fe1FeEe9E7b`
(challenge window 180 s). To try the click path without creating a market, open the demo market:
round 2 is open for `submit_interpretation`.

## Constructor arguments (MonocleFactory)

| # | Argument | Value | Set by |
| --- | --- | --- | --- |
| 1 | `monocle_code` | contents of `contracts/Monocle.py` | deploy script |
| 2 | `reputation_code` | contents of `contracts/MonocleReputation.py` (or `""` to skip) | deploy script |
| 3 | `creation_stake` | wei, default `0` | `CREATION_STAKE_WEI` |
| 4 | `min_interpretation_bond` | wei, default `10^15` (0.001 GEN) | `MIN_INTERPRETATION_BOND_WEI` |
| 5 | `min_source_bond` | wei, default `10^14` (0.0001 GEN) | `MIN_SOURCE_BOND_WEI` |
| 6 | `min_challenge_bond` | wei, default `10^15` (0.001 GEN) | `MIN_CHALLENGE_BOND_WEI` |
| 7 | `challenge_window_seconds` | 60 to 604800, default `3600` (use `180` for a demo) | `CHALLENGE_WINDOW_SECONDS` |

## Click path (happy path)

On the factory:

1. `create_monocle(sources, interpretation_type, title, description, schema_json)`, payable with
   ≥ `creation_stake`. Use two reachable https sources, for example
   `["https://en.wikipedia.org/wiki/Speed_of_light", "https://simple.wikipedia.org/wiki/Speed_of_light"]`.
   Read the new address from `get_monocles_page(count - 1, 1)`.

On the new Monocle:

2. `submit_interpretation(content, structured_claims_json)`, payable with ≥ `min_interpretation_bond`.
   Claims look like `{"claims": ["The speed of light in vacuum is exactly 299,792,458 m/s"]}`.
   Submit a second, wrong interpretation to see the choice.
3. `back_interpretation(interpretation_id)`, payable. This is optional.
4. `adjudicate()`. This can take minutes. The round becomes `decided_pending`, or `inconclusive` on
   thin evidence.
5. After `challenge_window_seconds`, call `finalize(round)`. `get_live_interpretation()` then
   reports `finality: "final"`.
6. `settle(round)`, then `claim(round)` from each backer. Winners are paid pro-rata; losers claim 0.

Optional challenge: during the window, call `challenge(round, alternative_id)` with ≥
`min_challenge_bond`, then `resolve_challenge(round)`.

## Fail-closed path

Create a Monocle whose two sources both return nothing (for example two non-existent pages), submit an
interpretation, then `adjudicate()`. The round becomes `inconclusive` with decision `no_evidence`,
and **no LLM call is made**. `claim(round)` refunds the full bond. The same happens on low
confidence (< 0.62), an unknown winner id, or fewer than 2 corroborating sources.

## Views agents use

* `get_live_interpretation()`: act **only** if `finality == "final"`.
* `get_round_info(round)`, `get_evidence_snapshot(round)`, `get_claim_scores(round)`.
* `get_claimable(round, address)`.

## Honest limit

Which interpretation best fits the fetched evidence is still an LLM's judgment. It is constrained by
claim-level scoring, cited evidence, corroboration and confidence gates, independent validators and
a challenge window, but it is not mechanical. See [LIMITATIONS.md](LIMITATIONS.md).
