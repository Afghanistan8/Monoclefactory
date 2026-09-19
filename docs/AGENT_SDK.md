# Agent SDK

MONOCLE has no UI. Agents talk to it through `sdk/typescript` (genlayer-js `2.0.0-rc.1`) or
`sdk/python` (genlayer-py `0.19.0rc2`), or directly with those libraries using the calls below.
Everything defaults to **Studio Next** (chain 61997, RPC `https://studio-dev.genlayer.com/api`).

## Install

```bash
npm install genlayer-js@2.0.0-rc.1
```

Or use this repo's workspace: `import { … } from "@monocle/sdk"`.

## Finality rules for agents

1. **Only act on `get_live_interpretation().finality === "final"`.** That output survived its
   challenge window. `"pending"` is a verdict that can still be overturned.
2. **Wait for FINALIZED consensus** on every write whose result anything acts on: `create_monocle`,
   `adjudicate`, `resolve_challenge`, `finalize`, `settle`, `claim`. The SDKs do this by default.
3. **Success means `FINISHED_WITH_RETURN`.** A revert reaches ACCEPTED/FINALIZED with
   `FINISHED_WITH_ERROR`. `describeTransactionOutcome()` / `describe_transaction_outcome()` treat
   that as failure, and `waitFinalized` throws `MonocleTransactionError`.
4. **Numbers are strings.** Wei is `BigInt(x)`. Confidence and scores are `Number(x)`.

## Clients

```ts
import {
  createReadClient, createWriteClient, MonocleCalls, FactoryCalls, ReputationCalls, studioNext,
} from "@monocle/sdk";

const reader = createReadClient();                        // keyless, Studio Next
const writer = createWriteClient({ account: process.env.AGENT_PRIVATE_KEY as `0x${string}` });

const factory = new FactoryCalls(writer, FACTORY_ADDRESS);
const monocle = new MonocleCalls(writer, MONOCLE_ADDRESS);
const monocleRead = new MonocleCalls(reader, MONOCLE_ADDRESS);
```

Every SDK write does four things:

1. `client.estimateTransactionFeesForWrite({address, functionName, args, value})`, which uses the
   v0.6 fee estimator and includes internal-message allocations;
2. `client.writeContract({..., fees, consensusMaxRotations?})`;
3. `waitForTransactionReceipt({hash, waitUntil: "finalized"})`;
4. the strict outcome check.

The raw genlayer-js equivalent:

```ts
import { createClient, createAccount } from "genlayer-js";
import { studioDevnet } from "genlayer-js/chains";   // chain 61997, studio-dev RPC

const client = createClient({ chain: studioDevnet, account: createAccount(pk) });
const fees = await client.estimateTransactionFeesForWrite({
  address: monocleAddress, functionName: "adjudicate", args: [], value: 0n,
});
const hash = await client.writeContract({
  address: monocleAddress, functionName: "adjudicate", args: [], value: 0n,
  fees, consensusMaxRotations: 6,
});
const tx = await client.waitForTransactionReceipt({ hash, waitUntil: "finalized", fullTransaction: true });
if (!isSuccessful(tx) || tx.statusName !== "FINALIZED") throw new Error("not a finalized success");
```

## Read the live output

```ts
const live = await monocleRead.getLiveInterpretation();
if (live.has_live && live.finality === "final") {
  const r = live.reasoning;                 // AdjudicationRecord
  console.log(live.interpretation.content, r.confidence, r.evidence_hash);
  // r.evidence_snapshot: [{url, role, excerpt}] -- sliced from the REAL fetched page in contract code
  // r.claim_scores:      per-claim supported|contradicted|insufficient with cited source URLs
  // r.rollups:           deterministic per-interpretation roll-up (rollup_bps, corroborating_sources)
}
if (live.pending && "winner_id" in live.pending) {
  // A newer verdict is in its challenge window until live.pending.challenge_deadline.
}
// Or simply:
const actionable = await monocleRead.getFinalOutputOrNull();
```

## Create a Monocle

```ts
const { monocle: address } = await factory.createMonocle({
  sources: ["https://example.com/feed", "https://example.org/feed"],   // 2..8 http(s)
  interpretationType: "market",
  title: "BTC Dominance Trend",
  description: "Tracks the live narrative around BTC dominance.",
  schema: { direction: "up|down|flat" },   // every submission must include these fields
  // value defaults to get_creation_stake(); any excess is refunded
});
```

The address is resolved from the append-only registry index captured before the write. It does
not rely on decoding the write's return value.

## Full method reference

**Value**: `—` means no value; `bond` means it must be at least the Monocle's configured minimum.
**Finality** is what the SDK waits for.

### MonocleFactory

| Method | Kind | Args | Value | Finality | Notes |
| --- | --- | --- | --- | --- | --- |
| `create_monocle` | write, payable | `sources: string[], interpretation_type, title, description, schema_json` | ≥ `creation_stake` (excess refunded) | FINALIZED | Deploys a Monocle with you as creator |
| `withdraw_fees` | write | — | — | FINALIZED | Owner only. Pays `collected_fees + residual_fees` |
| `set_creation_stake` | write | `stake: int` | — | FINALIZED | Owner only. Future Monocles only |
| `receive_residual` | write, payable | — | > 0 | — | Called by `Monocle.flush_residual` |
| `get_owner` / `get_creation_stake` / `get_bond_config` | view | — | | | |
| `get_collected_fees` / `get_residual_fees` | view | — | | | |
| `get_monocles` / `get_monocles_count` | view | — | | | |
| `get_monocles_page` | view | `offset, limit` (≤100) | | | `{total, offset, addresses}` |
| `get_monocle_meta` | view | `address` | | | Creation-time metadata only |
| `is_registered` | view | `address` | | | Used by MonocleReputation |
| `get_monocles_by_creator` / `get_monocles_by_type` | view | `address` / `type` | | | |
| `get_reputation_address` / `get_vault_model` | view | — | | | |

### Monocle

| Method | Kind | Args | Value | Finality | Notes |
| --- | --- | --- | --- | --- | --- |
| `add_source` | write, payable | `url, role` | source bond | FINALIZED | Permissionless, append-only, ≤8 |
| `claim_source_bond` | write | `url` | — | FINALIZED | Adder only, after the source has fetched OK (or after close) |
| `submit_interpretation` | write, payable | `content, structured_claims_json` | interpretation bond | FINALIZED | Returns an id like `"3-7"`. Open round only |
| `back_interpretation` | write, payable | `interpretation_id` | > 0 | FINALIZED | Current open round only |
| `adjudicate` | write | — | — | FINALIZED, `consensusMaxRotations: 6` | Anyone. Returns the pending winner or `""` |
| `challenge` | write, payable | `round, alternative_interpretation_id` | challenge bond | FINALIZED | Only within 1h of the decision, one per round |
| `resolve_challenge` | write | `round` | — | FINALIZED, `consensusMaxRotations: 6` | Anyone. Returns `upheld | rejected | unresolved` and finalizes |
| `finalize` | write | `round` | — | FINALIZED | Anyone, after the window (or 24h after an unresolved challenge) |
| `settle` | write | `round` | — | FINALIZED | `finalized` rounds only |
| `claim` | write | `round` | — | FINALIZED | Payout or refund. Returns the amount as a string |
| `cancel_round` | write | `round` | — | FINALIZED | Anyone, 24h after open with no verdict |
| `close_monocle` / `cancel_close` | write | — | — | FINALIZED | Creator only |
| `finalize_close` | write | — | — | FINALIZED | Anyone, 2h after `close_monocle` |
| `flush_residual` | write | — | — | FINALIZED | Anyone, after close |
| `get_monocle_info` | view | — | | | Status, bonds, constants, live pointers |
| `get_live_interpretation` | view | — | | | `{has_live, finality, interpretation, reasoning, pending}` |
| `get_pending_interpretation` | view | — | | | Current round's pending verdict and challenge |
| `get_round_info` | view | `round` | | | Status, finality, pool/bonus/pot, winner, reasoning, challenge, ids |
| `get_round_interpretations` / `get_interpretation` | view | `round` / `id` | | | |
| `get_sources` | view | — | | | Role, adder, bond status, fetch history, content hash |
| `get_adjudication_log` | view | `offset, limit` (≤50) | | | `{total, offset, entries}` |
| `get_claimable` / `is_claimed` / `get_backing` | view | `round, address` (+ id) | | | Address matching is case-insensitive |
| `get_evidence_snapshot` | view | `round` | | | `{evidence_hash, evidence_snapshot, fetch_report}` |
| `get_claim_scores` | view | `round` | | | `{claim_scores, rollups, ranking}` |
| `get_reputation` | view | `address` | | | Local ledger for this Monocle |
| `get_current_round` / `get_finality` | view | — / `round` | | | |
| `get_round_outcome` | view | `round` | | | Stake-free outcome for reputation sync |
| `get_vault_state` | view | — | | | `{tracked, bucket_sum, native_balance, conserved, solvent, buckets}` |

### MonocleReputation

| Method | Kind | Args | Finality | Notes |
| --- | --- | --- | --- | --- |
| `record_round` | write | `monocle_address, round` | FINALIZED | Anyone. Registered Monocles and FINAL rounds only. Idempotent |
| `get_reputation` | view | `address` | | `{decided_wins, decided_losses, inconclusive_participations, challenges_won, challenges_lost, rounds_recorded, last_finalized_at}` |
| `is_recorded` / `get_recorded_round` / `get_recorded_page` | view | | | |
| `get_factory` | view | — | | |

## Typical agent loop

```ts
const m = new MonocleCalls(writer, MONOCLE);
const info = await m.getInfo();
const round = info.current_round;

// 1. Participate
const { transaction } = await m.submitInterpretation(
  "Dominance is rising on ETF inflows.",
  { claims: ["BTC dominance rose this week", "ETF inflows drove the move"], direction: "up" },
  BigInt(info.min_interpretation_bond),
);

// 2. Anyone adjudicates
await m.adjudicate();
const r = await m.getRoundInfo(round);
if (r.status === "decided_pending") {
  // 3. Optionally challenge within the window, else finalize after it
  // await m.challenge(round, betterId, BigInt(info.min_challenge_bond)); await m.resolveChallenge(round);
  // ... after r.challenge_deadline:
  await m.finalize(round);
  await m.settle(round);
}
// 4. Claim (payout, or refund for inconclusive/unchanged/cancelled rounds)
if ((await m.getClaimable(round, me)) !== "0") await m.claim(round);
```

## Python

```python
from monocle_sdk import create_read_client, create_write_client, MonocleClient

reader = MonocleClient(create_read_client(), MONOCLE)
live = reader.final_output_or_none()      # None unless finality == "final"

writer = MonocleClient(create_write_client(PRIVATE_KEY), MONOCLE)
writer.adjudicate()                       # fee-estimated, rotations=6, waits FINALIZED, strict outcome
```

## Settling against MONOCLE from another GenLayer contract

Cross-contract **views** are synchronous. Read outside any nondet block:

```python
import genlayer as gl
from genlayer.types import Address

class Consumer(gl.contract.Contract):
    @gl.public.write
    def act(self, monocle: str) -> None:
        live = gl.contract.get_at(Address(monocle)).view().get_live_interpretation()
        if not live["has_live"] or live["finality"] != "final":
            raise gl.vm.UserError("No FINAL MONOCLE output yet.")
        if float(live["reasoning"]["confidence"]) < 0.7:
            raise gl.vm.UserError("Confidence too low to act on.")
        # ... act on live["interpretation"]["claims"] / ["fields"]
```

Contract views read the state of the transaction being executed. When your own action is
irreversible, prefer outputs whose `live_since` is comfortably older than your own transaction.
