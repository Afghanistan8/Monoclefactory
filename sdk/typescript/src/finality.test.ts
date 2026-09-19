import assert from "node:assert/strict";
import { test } from "node:test";

import { ExecutionResult, TransactionStatus } from "genlayer-js/types";
import type { GenLayerTransaction, TransactionHash } from "genlayer-js/types";

import { describeTransactionOutcome, MonocleTransactionError, waitFinalized } from "./finality.js";
import { STUDIO_NEXT_CHAIN_ID, STUDIO_NEXT_RPC, studioNext, resolveNetwork } from "./networks.js";

const tx = (statusName: string, txExecutionResultName?: string) =>
  ({ statusName, txExecutionResultName }) as unknown as GenLayerTransaction;

test("FINALIZED + FINISHED_WITH_RETURN is the only unconditional success", () => {
  const out = describeTransactionOutcome(tx(TransactionStatus.FINALIZED, ExecutionResult.FINISHED_WITH_RETURN));
  assert.equal(out.succeeded, true);
  assert.equal(out.finalized, true);
});

test("ACCEPTED + FINISHED_WITH_ERROR is a failure (revert reported as success)", () => {
  const out = describeTransactionOutcome(tx(TransactionStatus.ACCEPTED, ExecutionResult.FINISHED_WITH_ERROR), {
    requireFinalized: false,
  });
  assert.equal(out.succeeded, false);
  assert.match(out.reason ?? "", /reverted/);
});

test("FINALIZED + FINISHED_WITH_ERROR is still a failure", () => {
  assert.equal(describeTransactionOutcome(tx(TransactionStatus.FINALIZED, ExecutionResult.FINISHED_WITH_ERROR)).succeeded, false);
});

test("missing or NOT_VOTED execution results are never defaulted to success", () => {
  assert.equal(describeTransactionOutcome(tx(TransactionStatus.FINALIZED)).succeeded, false);
  assert.equal(describeTransactionOutcome(tx(TransactionStatus.FINALIZED, ExecutionResult.NOT_VOTED)).succeeded, false);
  assert.equal(describeTransactionOutcome(tx(TransactionStatus.FINALIZED, ExecutionResult.NONDET_DISAGREE)).succeeded, false);
});

test("ACCEPTED success is not enough when finality is required (default)", () => {
  const accepted = tx(TransactionStatus.ACCEPTED, ExecutionResult.FINISHED_WITH_RETURN);
  assert.equal(describeTransactionOutcome(accepted).succeeded, false);
  assert.equal(describeTransactionOutcome(accepted, { requireFinalized: false }).succeeded, true);
});

test("failed terminal statuses are failures", () => {
  for (const s of [TransactionStatus.UNDETERMINED, TransactionStatus.CANCELED, TransactionStatus.LEADER_TIMEOUT, TransactionStatus.VALIDATORS_TIMEOUT]) {
    assert.equal(describeTransactionOutcome(tx(s, ExecutionResult.FINISHED_WITH_RETURN)).succeeded, false, s);
  }
});

test("waitFinalized throws MonocleTransactionError on a reverted-but-finalized tx", async () => {
  const client = {
    waitForTransactionReceipt: async () => tx(TransactionStatus.FINALIZED, ExecutionResult.FINISHED_WITH_ERROR),
  };
  await assert.rejects(waitFinalized(client, "0x01" as TransactionHash), MonocleTransactionError);
});

test("waitFinalized asks the SDK for finalized receipts", async () => {
  let seen: unknown;
  const client = {
    waitForTransactionReceipt: async (args: unknown) => {
      seen = args;
      return tx(TransactionStatus.FINALIZED, ExecutionResult.FINISHED_WITH_RETURN);
    },
  };
  await waitFinalized(client, "0x01" as TransactionHash);
  assert.equal((seen as { waitUntil: string }).waitUntil, "finalized");
});

test("Studio Next preset is chain 61997 on the canonical studio-dev RPC", () => {
  assert.equal(studioNext.id, STUDIO_NEXT_CHAIN_ID);
  assert.equal(studioNext.id, 61997);
  assert.deepEqual(studioNext.rpcUrls.default.http, [STUDIO_NEXT_RPC]);
  assert.equal(STUDIO_NEXT_RPC, "https://studio-dev.genlayer.com/api");
  assert.equal(resolveNetwork(undefined).id, 61997);
  assert.equal(resolveNetwork("localnet").id, 61127);
  assert.throws(() => resolveNetwork("studionet"));
  assert.throws(() => resolveNetwork("bradbury"));
});

test("fee estimation falls back to the generic estimate when simulation fails", async () => {
  const { estimateFees } = await import("./client.js");
  let generic = 0;
  const client = {
    estimateTransactionFeesForWrite: async () => {
      throw new Error("sim_estimateTransactionFees: execution failed");
    },
    estimateTransactionFees: async () => {
      generic += 1;
      return { feeValue: 1n };
    },
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fees = await estimateFees(client as any, "0x0000000000000000000000000000000000000001", "finalize", ["1"], 0n);
  assert.equal(generic, 1);
  assert.deepEqual(fees, { feeValue: 1n });
});

test("fee estimation prefers the per-call simulation when it works", async () => {
  const { estimateFees } = await import("./client.js");
  const client = {
    estimateTransactionFeesForWrite: async () => ({ feeValue: 7n }),
    estimateTransactionFees: async () => {
      throw new Error("should not be called");
    },
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  assert.deepEqual(await estimateFees(client as any, "0x0000000000000000000000000000000000000001", "claim", ["1"], 0n), { feeValue: 7n });
});

test("read retries 'server busy' but never retries a rate limit", async () => {
  const { read } = await import("./client.js");
  let calls = 0;
  const busy = {
    readContract: async () => {
      calls += 1;
      if (calls < 3) throw new Error("Server busy: all 8 execution slots occupied, retry later");
      return "ok";
    },
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  assert.equal(await read(busy as any, "0x0000000000000000000000000000000000000001", "x", [], { delayMs: 1 }), "ok");
  assert.equal(calls, 3);

  let limited = 0;
  const rate = {
    readContract: async () => {
      limited += 1;
      throw new Error("Rate limit exceeded: 500 requests per hour");
    },
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  await assert.rejects(read(rate as any, "0x0000000000000000000000000000000000000001", "x", [], { delayMs: 1 }), /Rate limit/);
  assert.equal(limited, 1);
});
