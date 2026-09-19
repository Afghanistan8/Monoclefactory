# MONOCLE Intelligent Contracts

**A capital-backed, claim-level, challengeable interpretation engine on GenLayer.** This repository is
the MONOCLE **Intelligent Contract system only**: an exact build of the contracts in
[Afghanistan8/Monocle](https://github.com/Afghanistan8/Monocle), deployed separately. No web app
lives here.

Anyone opens a Monocle on two or more live web sources. Participants bond GEN behind competing
interpretations written as structured claims. When anyone triggers adjudication, the leader fetches
every source fresh, and validators independently re-fetch and re-reason. Each claim is scored
supported, contradicted or insufficient against the evidence actually fetched.

A confident, corroborated verdict becomes **pending**. It becomes the **FINAL** live output once the
challenge window passes or a bonded challenge resolves. No evidence, low confidence or an invalid
verdict never moves stake: everyone is refunded. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Contracts

| File | Role | Key methods |
| --- | --- | --- |
| [`contracts/MonocleFactory.py`](contracts/MonocleFactory.py) | Registry + CREATE2 factory. Embeds the Monocle and Reputation source, deploys the reputation aggregator in its constructor, charges exactly `creation_stake` and refunds excess | `create_monocle`, `withdraw_fees`, `set_creation_stake`, `receive_residual`; views `get_monocles_page`, `get_monocle_meta`, `get_bond_config`, `get_reputation_address`, `is_registered` |
| [`contracts/Monocle.py`](contracts/Monocle.py) | One engine: sources, interpretations, adjudication, challenge window, finality, settlement, timelocked close, local reputation, and its own custody ledger (`MonocleVault`) | `add_source`, `submit_interpretation`, `back_interpretation`, `adjudicate`, `challenge`, `resolve_challenge`, `finalize`, `settle`, `claim`, `cancel_round`, `close_monocle`, `finalize_close`; view `get_live_interpretation` |
| [`contracts/MonocleReputation.py`](contracts/MonocleReputation.py) | Pull-based cross-Monocle reputation (views only, never an input to judgment) | `record_round`, `get_reputation` |

The full method table is in [docs/AGENT_SDK.md](docs/AGENT_SDK.md). Agents act only on
`get_live_interpretation().finality == "final"`.

## Install

Python 3.12 with the locked Consensus v0.6 RC family (genlayer-py 0.19 RC, genlayer-test 0.30 RC,
genvm-linter 0.11.1 RC):

```bash
pip install .
```

Node 22+ for the deploy scripts and TypeScript SDK (genlayer-js 2.0 RC):

```bash
npm install
```

## Lint and test

```bash
npm run lint:contracts
```

```bash
gltest tests/direct -v
```

The direct suite runs in-process against the GenVM WASI mock, including real multi-contract
factory → Monocle → Reputation flows, with no live node. `GENVM_VERSION=v0.6.0-rc5` is pinned.

```bash
npm run typecheck
```

```bash
npm run test:sdk
```

The integration suite targets a live node and skips cleanly without one:

```bash
gltest tests/integration -v --network studio_devnet
```

## Deploy (new, separate deployment)

A fresh deploy creates a **new** factory. `deploy/deployments.json` starts empty and records only
what you deploy.

```bash
cp .env.example .env
```

Put a Studio Next-funded key in `.env` (`PRIVATE_KEY`, or `KEYSTORE_PATH` + `KEYSTORE_PASSWORD`). Get
GEN from the faucet at https://studio-next.genlayer.com. Optional settings: `CREATION_STAKE_WEI`,
`MIN_*_BOND_WEI`, and `CHALLENGE_WINDOW_SECONDS` (default 3600; 180 makes a quick demo).

```bash
npm run deploy:studio-next
```

```bash
npm run check:studio-next
```

Optionally seed a demo market and run its whole lifecycle (create, submit ×2, adjudicate, wait
out the window, finalize, settle, claim). Every transaction hash is recorded in `deploy/deployments.json`:

```bash
npm run seed:studio-next
```

After a Studio Next reset, `check:studio-next` reports the factory has no code; rerun the deploy and
seed commands. See [docs/STUDIO_NEXT.md](docs/STUDIO_NEXT.md) and [docs/PORTAL.md](docs/PORTAL.md).

| | |
| --- | --- |
| Network | GenLayer Studio Next (browser: https://studio-next.genlayer.com) |
| RPC | https://studio-dev.genlayer.com/api |
| Chain ID | 61997 |
| Explorer | https://explorer-studio-dev.genlayer.com |
| Local | localnet, 61127 |

## Layout

```
contracts/          Monocle.py, MonocleFactory.py, MonocleReputation.py
tests/direct/       gltest direct-mode + in-process multi-contract system tests
tests/integration/  live-node lifecycle (skips without a node)
deploy/             deploy, seed and health-check scripts; deployments.json
sdk/typescript/     @monocle/sdk (strict FINALIZED + FINISHED_WITH_RETURN outcomes)
sdk/python/         monocle_sdk
docs/               ARCHITECTURE, RESOLUTION_LOGIC, AGENT_SDK, AUDIT, LIMITATIONS, STUDIO_NEXT, PORTAL
```

## Honest limits

Deciding which interpretation fits the fetched text is still, ultimately, a language model's
judgment. MONOCLE narrows that judgment and makes it auditable. It does not make it mechanical.
Read [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

## License

MIT
