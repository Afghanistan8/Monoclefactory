/**
 * Seeds a live market on the recorded factory and runs it through a real
 * adjudication. With --full it also waits out the challenge window and runs
 * finalize -> settle -> claim, proving the whole lifecycle on chain.
 *
 *   npx tsx --env-file-if-exists=.env deploy/002_open_test_market.ts [studio-next|localnet] [--full]
 *   npx tsx --env-file-if-exists=.env deploy/002_open_test_market.ts [network] --finish   # finish the recorded market
 *
 * Records the market and every transaction hash in deploy/deployments.json
 * (testMonocle, createTx, submitTxs, adjudicateTx, roundStatus, finalizeTx).
 * Spends: 2 x min_interpretation_bond + network fees (claim returns the pool).
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { createWriteClient, FactoryCalls, MonocleCalls, resolveNetwork, type Address } from "../sdk/typescript/src/index.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const RECORD_PATH = join(ROOT, "deploy", "deployments.json");
const EXPLORER = "https://explorer-studio-dev.genlayer.com/address";

function step(msg: string) {
  console.log(`\n[${new Date().toISOString().slice(11, 19)}] ${msg}`);
}

function saveRecord(network: string, patch: Record<string, unknown>) {
  const records = JSON.parse(readFileSync(RECORD_PATH, "utf-8"));
  records[network] = { ...records[network], ...patch };
  writeFileSync(RECORD_PATH, JSON.stringify(records, null, 2) + "\n");
}

/**
 * finalize -> settle -> claim for round 1. The node simulates each write at
 * its own consensus time, which can lag the local clock by tens of seconds,
 * so "window still open" during fee estimation is retried, not fatal.
 */
async function finishLifecycle(m: MonocleCalls, networkName: string) {
  let round = await m.getRoundInfo("1");
  if (round.status === "decided_pending") {
    const waitMs = Math.max(0, Number(round.challenge_deadline) * 1000 - Date.now()) + 15_000;
    if (waitMs > 15_000) {
      step(`Waiting ${Math.round(waitMs / 1000)}s for the challenge window…`);
      await new Promise((r) => setTimeout(r, waitMs));
    }
    for (let attempt = 1; ; attempt++) {
      step(`Finalizing (attempt ${attempt})…`);
      try {
        const fin = await m.finalize("1");
        saveRecord(networkName, { finalizeTx: fin.hash });
        break;
      } catch (err) {
        if (attempt >= 8) throw err;
        const first = (err as Error).message.split(/\r?\n/)[0];
        console.log(`  not yet (${first}); chain time may lag, retrying in 20s`);
        await new Promise((r) => setTimeout(r, 20_000));
      }
    }
    round = await m.getRoundInfo("1");
  }
  if (round.status === "finalized") {
    step("Settling…");
    await m.settle("1");
    round = await m.getRoundInfo("1");
  }
  let claimTx = "";
  if (["settled", "inconclusive", "unchanged", "cancelled"].includes(round.status)) {
    step("Claiming…");
    claimTx = (await m.claim("1")).hash;
    round = await m.getRoundInfo("1");
  }
  saveRecord(networkName, { roundStatus: round.status, ...(claimTx ? { claimTx: claimTx } : {}) });
  const live = await m.getLiveInterpretation();
  console.log(`Round 1: ${round.status}; live finality: ${live.finality}; current round: ${(await m.getInfo()).current_round}`);
}

async function main() {
  const networkName = process.argv.find((a, i) => i >= 2 && !a.startsWith("--")) ?? "studio-next";
  const full = process.argv.includes("--full");
  const finishOnly = process.argv.includes("--finish");
  const chain = resolveNetwork(networkName);
  const records = JSON.parse(readFileSync(RECORD_PATH, "utf-8"));
  const factoryAddress = records[networkName]?.factory as Address | undefined;
  if (!factoryAddress) throw new Error(`No factory recorded for ${networkName}; run 001 first.`);
  const key = process.env.PRIVATE_KEY as `0x${string}` | undefined;
  if (!key) throw new Error("Set PRIVATE_KEY in .env");

  const client = createWriteClient({ account: key, chain });
  const factory = new FactoryCalls(client, factoryAddress);

  if (finishOnly) {
    const recorded = records[networkName]?.testMonocle as Address | undefined;
    if (!recorded) throw new Error("No seeded market recorded; run without --finish first.");
    await finishLifecycle(new MonocleCalls(client, recorded), networkName);
    step(`Done. Market ${recorded}: ${EXPLORER}/${recorded}`);
    return;
  }

  step(`Creating Monocle on factory ${factoryAddress}…`);
  const created = await factory.createMonocle({
    sources: ["https://en.wikipedia.org/wiki/Speed_of_light", "https://simple.wikipedia.org/wiki/Speed_of_light"],
    interpretationType: "research",
    title: "What is the speed of light in vacuum?",
    description: "Seeded demo market: which interpretation do the cited encyclopedia pages support?",
    schema: { value_m_per_s: "speed of light in metres per second" },
  });
  const monocle = created.monocle;
  console.log(`Monocle: ${monocle}  (tx ${created.hash})`);
  saveRecord(networkName, {
    testMonocle: monocle,
    createTx: created.hash,
    submitTxs: [],
    adjudicateTx: "",
    roundStatus: "open",
  });

  const m = new MonocleCalls(client, monocle);
  const info = await m.getInfo();
  const bond = BigInt(info.min_interpretation_bond);
  console.log(`Challenge window on this Monocle: ${info.challenge_window_seconds}s`);

  step("Submitting the correct interpretation…");
  const s1 = await m.submitInterpretation(
    "Light in vacuum travels at exactly 299,792,458 metres per second; the metre is defined from it.",
    {
      claims: [
        "The speed of light in vacuum is exactly 299,792,458 metres per second",
        "The metre is defined using the speed of light",
      ],
      value_m_per_s: "299792458",
    },
    bond,
  );
  step("Submitting a wrong interpretation…");
  const s2 = await m.submitInterpretation(
    "Light in vacuum travels at roughly 150,000 kilometres per second.",
    { claims: ["The speed of light in vacuum is about 150,000 kilometres per second"], value_m_per_s: "150000000" },
    bond,
  );
  saveRecord(networkName, { submitTxs: [s1.hash, s2.hash] });

  step("Adjudicating (validators fetch both pages and judge every claim)…");
  const adj = await m.adjudicate();
  const round = await m.getRoundInfo("1");
  saveRecord(networkName, { adjudicateTx: adj.hash, roundStatus: round.status });
  const reasoning = round.reasoning as Record<string, unknown>;
  console.log(
    JSON.stringify(
      {
        status: round.status,
        decision: reasoning.decision,
        pending_winner: round.pending_winner,
        confidence: reasoning.confidence,
        sources_fetched_ok: reasoning.sources_fetched_ok,
        challenge_deadline: round.challenge_deadline,
      },
      null,
      2,
    ),
  );

  if (full && round.status === "decided_pending") await finishLifecycle(m, networkName);

  step(`Done. Market ${monocle}: ${EXPLORER}/${monocle}`);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
