/**
 * Deploys the MONOCLE system: MonocleFactory, with Monocle.py and
 * MonocleReputation.py embedded as constructor arguments. The factory's
 * constructor deploys the MonocleReputation aggregator itself.
 *
 *   PRIVATE_KEY=0x... npx tsx deploy/001_deploy_monocle_system.ts [studio-next|localnet]
 *   KEYSTORE_PATH=... KEYSTORE_PASSWORD=... npx tsx deploy/001_deploy_monocle_system.ts
 *
 * Idempotent-enough for Studio Next, whose state can be reset: the last
 * deployment per network is recorded in deploy/deployments.json together
 * with a hash of the three contract sources. On re-run, if that factory
 * still has code AND the sources are unchanged, nothing is deployed. If the
 * network was reset (no code at the address) or sources changed, a fresh
 * system is deployed and the record is replaced.
 *
 * Fees use the SDK's v0.6 estimator; waits require FINALIZED plus a genuine
 * FINISHED_WITH_RETURN (sdk/typescript/src/finality.ts).
 */
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { Wallet } from "ethers";
import { createAccount, createClient } from "genlayer-js";
import type { TransactionHash } from "genlayer-js/types";

import { waitFinalized } from "../sdk/typescript/src/finality.js";
import { resolveNetwork } from "./networks.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONTRACTS = join(ROOT, "contracts");
const RECORD_PATH = join(ROOT, "deploy", "deployments.json");

type DeploymentRecord = {
  network: string;
  chainId: number;
  factory: `0x${string}`;
  reputation: string;
  sourcesHash: string;
  deployTx: string;
  deployedAt: string;
  config: Record<string, string>;
};

async function resolvePrivateKey(): Promise<`0x${string}`> {
  if (process.env.PRIVATE_KEY) return process.env.PRIVATE_KEY as `0x${string}`;
  const { KEYSTORE_PATH, KEYSTORE_PASSWORD } = process.env;
  if (KEYSTORE_PATH && KEYSTORE_PASSWORD) {
    const wallet = await Wallet.fromEncryptedJson(readFileSync(KEYSTORE_PATH, "utf-8"), KEYSTORE_PASSWORD);
    return wallet.privateKey as `0x${string}`;
  }
  throw new Error("Set PRIVATE_KEY, or KEYSTORE_PATH + KEYSTORE_PASSWORD, before deploying.");
}

function readRecords(): Record<string, DeploymentRecord> {
  return existsSync(RECORD_PATH) ? JSON.parse(readFileSync(RECORD_PATH, "utf-8")) : {};
}

function bigintEnv(name: string, fallback: bigint): bigint {
  const raw = process.env[name];
  return raw === undefined || raw === "" ? fallback : BigInt(raw);
}

async function main() {
  const networkName = process.argv[2] ?? process.env.MONOCLE_NETWORK ?? "studio-next";
  const chain = resolveNetwork(networkName);

  const monocleSource = readFileSync(join(CONTRACTS, "Monocle.py"), "utf-8");
  const factorySource = readFileSync(join(CONTRACTS, "MonocleFactory.py"), "utf-8");
  const reputationSource = readFileSync(join(CONTRACTS, "MonocleReputation.py"), "utf-8");
  const sourcesHash = createHash("sha256").update(monocleSource).update(factorySource).update(reputationSource).digest("hex");

  const config = {
    creationStake: bigintEnv("CREATION_STAKE_WEI", 0n),
    minInterpretationBond: bigintEnv("MIN_INTERPRETATION_BOND_WEI", 10n ** 15n),
    minSourceBond: bigintEnv("MIN_SOURCE_BOND_WEI", 10n ** 14n),
    minChallengeBond: bigintEnv("MIN_CHALLENGE_BOND_WEI", 10n ** 15n),
    // Seconds a verdict stays challengeable, for every Monocle this factory
    // creates. 3600 for real use; the public Studio Next demo uses 180.
    challengeWindowSeconds: bigintEnv("CHALLENGE_WINDOW_SECONDS", 3600n),
  };
  const configRecord = Object.fromEntries(Object.entries(config).map(([k, v]) => [k, v.toString()]));

  const account = createAccount(await resolvePrivateKey());
  const client = createClient({ chain, account });

  const records = readRecords();
  const previous = records[networkName];
  const sameConfig = previous && JSON.stringify(previous.config) === JSON.stringify(configRecord);
  if (previous && previous.sourcesHash === sourcesHash && sameConfig && !process.env.FORCE_REDEPLOY) {
    const code = await client.getContractCode(previous.factory).catch(() => "");
    if (code && code.length > 0) {
      console.log(`MonocleFactory already deployed on ${chain.name} at ${previous.factory} with identical sources and config; nothing to do.`);
      console.log("Set FORCE_REDEPLOY=1 to deploy a fresh system anyway.");
      return;
    }
    console.log(`Recorded factory ${previous.factory} has no code (Studio Next reset?). Redeploying.`);
  }

  console.log(`Deploying MONOCLE to ${chain.name} (chain ${chain.id}) as ${account.address}`);
  console.log(
    `  creation stake ${config.creationStake} wei, bonds: interpretation ${config.minInterpretationBond}, ` +
      `source ${config.minSourceBond}, challenge ${config.minChallengeBond}, challenge window ${config.challengeWindowSeconds}s`,
  );

  // v0.6: every deploy/write carries a quoted fee distribution. The factory
  // constructor emits one internal deploy message (MonocleReputation).
  const fees = await client.estimateTransactionFees();
  const hash = (await client.deployContract({
    code: factorySource,
    args: [
      monocleSource,
      reputationSource,
      config.creationStake,
      config.minInterpretationBond,
      config.minSourceBond,
      config.minChallengeBond,
      config.challengeWindowSeconds,
    ],
    fees,
  })) as TransactionHash;
  console.log(`Deploy tx: ${hash} -- waiting for FINALIZED...`);
  const tx = await waitFinalized(client, hash);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const anyTx = tx as any;
  const factory = (anyTx.txDataDecoded?.contractAddress ?? anyTx.contractAddress ?? anyTx.to_address) as `0x${string}`;
  if (!factory) throw new Error(`Deploy finalized but no contract address in receipt: ${JSON.stringify(anyTx)}`);

  // The reputation contract is deployed by an internal message that executes
  // after the factory's transaction finalizes, so it can lag. Poll for it.
  const reputation = (await client.readContract({ address: factory, functionName: "get_reputation_address", args: [] })) as string;
  let reputationCode = "";
  for (let attempt = 0; attempt < 24 && reputation && !reputationCode; attempt++) {
    reputationCode = await client.getContractCode(reputation as `0x${string}`).catch(() => "");
    if (!reputationCode) await new Promise((r) => setTimeout(r, 5000));
  }
  if (!reputationCode) {
    console.warn(
      `Warning: MonocleReputation at ${reputation || "(none)"} still has no code after 2 minutes. Its deploy is an ` +
        "internal message that runs after the factory finalizes; run `npm run check:studio-next` again shortly. " +
        "If it never appears, the constructor's message budget was too low: redeploy with FORCE_REDEPLOY=1.",
    );
  }

  records[networkName] = {
    network: networkName,
    chainId: chain.id,
    factory,
    reputation,
    sourcesHash,
    deployTx: hash,
    deployedAt: new Date().toISOString(),
    config: configRecord,
  };
  writeFileSync(RECORD_PATH, JSON.stringify(records, null, 2) + "\n");

  console.log(`\nMonocleFactory:     ${factory}`);
  console.log(`MonocleReputation:  ${reputation}${reputationCode ? "" : " (code pending)"}`);
  const explorer = chain.blockExplorers?.default?.url;
  if (explorer) console.log(`Explorer:           ${explorer}/address/${factory}`);
  console.log(`Recorded in deploy/deployments.json under "${networkName}".`);
  console.log("Next: npm run check:studio-next, then optionally npm run seed:studio-next.");
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
