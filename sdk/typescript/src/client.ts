import { createAccount, createClient } from "genlayer-js";
import type { GenLayerChain, GenLayerClient, TransactionHash, CalldataEncodable } from "genlayer-js/types";

import { waitDecided, waitFinalized } from "./finality.js";
import type { Address } from "./types.js";
import { studioNext } from "./networks.js";

export type MonocleClient = GenLayerClient<GenLayerChain>;

let sharedReadClient: MonocleClient | null = null;

/**
 * Keyless read client. No account is created: a throwaway random account
 * per read is a known GenLayer anti-pattern (wallet prompts, noise).
 */
export function createReadClient(chain: GenLayerChain = studioNext): MonocleClient {
  if (chain === studioNext) {
    sharedReadClient ??= createClient({ chain }) as MonocleClient;
    return sharedReadClient;
  }
  return createClient({ chain }) as MonocleClient;
}

export interface WriteClientOptions {
  /** A private key (agents) or an existing viem/genlayer-js Account. */
  account: `0x${string}` | ReturnType<typeof createAccount>;
  chain?: GenLayerChain;
}

export function createWriteClient({ account, chain = studioNext }: WriteClientOptions): MonocleClient {
  const acct = typeof account === "string" ? createAccount(account) : account;
  return createClient({ chain, account: acct }) as MonocleClient;
}

export interface WriteOptions {
  value?: bigint;
  consensusMaxRotations?: number;
  /** Default true: wait for FINALIZED. Only pass false for writes nobody acts on. */
  requireFinalized?: boolean;
}

/**
 * Fee estimate for one write. Prefers a simulation of this exact call (it
 * sizes internal-message budgets for payouts/deploys). Studio Next simulates
 * at a stale timestamp, so time-gated calls such as finalize() can fail in
 * simulation while succeeding on chain; then fall back to the generic v0.6
 * estimate and let consensus decide. A genuine revert still surfaces as
 * FINISHED_WITH_ERROR and is rejected by the strict outcome check.
 */
export async function estimateFees(
  client: MonocleClient,
  address: Address,
  functionName: string,
  args: CalldataEncodable[],
  value: bigint,
) {
  try {
    return await client.estimateTransactionFeesForWrite({ address, functionName, args, value });
  } catch {
    return await client.estimateTransactionFees();
  }
}

/**
 * Every write: SDK fee estimation from a simulation of this exact call
 * (includes internal-message allocations for payouts/deploys), then submit,
 * then wait and apply the strict outcome check.
 */
export async function writeAndWait(
  client: MonocleClient,
  address: Address,
  functionName: string,
  args: CalldataEncodable[],
  { value = 0n, consensusMaxRotations, requireFinalized = true }: WriteOptions = {},
) {
  const fees = await estimateFees(client, address, functionName, args, value);
  const hash = (await client.writeContract({
    address,
    functionName,
    args,
    value,
    fees,
    ...(consensusMaxRotations ? { consensusMaxRotations } : {}),
  })) as TransactionHash;
  const transaction = requireFinalized ? await waitFinalized(client, hash) : await waitDecided(client, hash);
  return { hash, transaction };
}

/** Studio Next's public RPC: "Server busy: all N execution slots occupied". */
export function isTransientRpcError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /server busy|execution slots occupied/i.test(msg);
}

/** Per-visitor quota on the public RPC (e.g. "Rate limit exceeded: 500 requests per hour"). */
export function isRateLimitError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /rate limit|too many requests|429/i.test(msg);
}

/**
 * View call. Retries only "server busy" (transient, clears in seconds).
 * A rate-limit error is never retried: retrying spends more of the quota.
 */
export async function read<T>(
  client: MonocleClient,
  address: Address,
  functionName: string,
  args: CalldataEncodable[] = [],
  { retries = 2, delayMs = 1500 }: { retries?: number; delayMs?: number } = {},
): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      return (await client.readContract({ address, functionName, args, jsonSafeReturn: true })) as T;
    } catch (err) {
      if (attempt >= retries || !isTransientRpcError(err) || isRateLimitError(err)) throw err;
      await new Promise((r) => setTimeout(r, delayMs * (attempt + 1)));
    }
  }
}
