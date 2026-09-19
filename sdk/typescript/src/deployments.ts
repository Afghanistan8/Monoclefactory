/**
 * deploy/deployments.json -> the small, validated shape agents and tools
 * use to find the deployed factory. Pure and dependency-free.
 */
import type { Address } from "./types.js";

const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;

export interface SeededMarket {
  address: Address;
  createTx: string;
  submitTxs: string[];
  adjudicateTx: string;
  roundStatus: string;
  liveUrl: string;
}

export interface DeploymentDefaults {
  network: string;
  chainId: number;
  factory: Address | "";
  reputation: Address | "";
  deployedAt: string;
  challengeWindowSeconds: number;
  testMonocle: Address | "";
  seeded: SeededMarket | null;
}

function addr(value: unknown): Address | "" {
  return typeof value === "string" && ADDRESS_RE.test(value) ? (value as Address) : "";
}

export function deploymentDefaults(records: unknown, network = "studio-next"): DeploymentDefaults {
  const all = (records && typeof records === "object" ? records : {}) as Record<string, Record<string, unknown>>;
  const rec = all[network] ?? {};
  const config = (rec.config ?? {}) as Record<string, unknown>;
  const window = Number(config.challengeWindowSeconds ?? rec.challengeWindowSeconds ?? 3600);
  const testMonocle = addr(rec.testMonocle);
  const seeded: SeededMarket | null = testMonocle
    ? {
        address: testMonocle,
        createTx: String(rec.createTx ?? ""),
        submitTxs: Array.isArray(rec.submitTxs) ? rec.submitTxs.map(String) : [],
        adjudicateTx: String(rec.adjudicateTx ?? ""),
        roundStatus: String(rec.roundStatus ?? ""),
        liveUrl: String(rec.liveUrl ?? ""),
      }
    : null;
  return {
    network,
    chainId: Number(rec.chainId ?? 0),
    factory: addr(rec.factory),
    reputation: addr(rec.reputation),
    deployedAt: String(rec.deployedAt ?? ""),
    challengeWindowSeconds: Number.isFinite(window) && window > 0 ? window : 3600,
    testMonocle,
    seeded,
  };
}
