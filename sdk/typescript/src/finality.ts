/**
 * Strict transaction-outcome logic.
 *
 * A terminal consensus status only means the network agreed on AN outcome.
 * A contract that reverts with gl.vm.UserError still reaches ACCEPTED /
 * FINALIZED with txExecutionResultName = FINISHED_WITH_ERROR. A UI that only
 * checks statusName shows a green check for exactly that case. The rule here, and
 * the v0.6 migration guide's rule, is:
 *
 *   success  <=>  status in {ACCEPTED, FINALIZED}
 *             AND txExecutionResultName === FINISHED_WITH_RETURN
 *
 * plus, for writes whose output other parties act on, status === FINALIZED.
 * Missing or NOT_VOTED execution results are never defaulted to success.
 */
import { isSuccessful } from "genlayer-js";
import { ExecutionResult, TransactionStatus } from "genlayer-js/types";
import type { GenLayerTransaction, TransactionHash } from "genlayer-js/types";

export interface TransactionOutcome {
  succeeded: boolean;
  finalized: boolean;
  status: string;
  executionResult: string;
  reason: string | null;
}

export interface OutcomeOptions {
  /** Require FINALIZED (not just ACCEPTED). Default true. */
  requireFinalized?: boolean;
}

const ACCEPTED_OR_FINAL = new Set<string>([TransactionStatus.ACCEPTED, TransactionStatus.FINALIZED]);

export const FAILED_TERMINAL_STATUSES = new Set<string>([
  TransactionStatus.UNDETERMINED,
  TransactionStatus.CANCELED,
  TransactionStatus.LEADER_TIMEOUT,
  TransactionStatus.VALIDATORS_TIMEOUT,
]);

export function describeTransactionOutcome(
  transaction: Pick<GenLayerTransaction, "statusName" | "txExecutionResultName"> & Partial<GenLayerTransaction>,
  { requireFinalized = true }: OutcomeOptions = {},
): TransactionOutcome {
  const status = String(transaction.statusName ?? "unknown");
  const result = String(transaction.txExecutionResultName ?? "missing");
  const finalized = status === TransactionStatus.FINALIZED;
  const base = { status, executionResult: result, finalized };

  if (!ACCEPTED_OR_FINAL.has(status)) {
    return { ...base, succeeded: false, reason: `Transaction did not reach an accepted consensus status (status: ${status}).` };
  }
  if (result === ExecutionResult.FINISHED_WITH_ERROR) {
    return {
      ...base,
      succeeded: false,
      reason: "Consensus was reached but the contract reverted (FINISHED_WITH_ERROR); no state changed.",
    };
  }
  if (result !== ExecutionResult.FINISHED_WITH_RETURN) {
    return {
      ...base,
      succeeded: false,
      reason: `Execution result is not a confirmed success (${result}); refusing to treat it as one.`,
    };
  }
  // Defence in depth: the SDK's own v0.6 predicate must agree.
  if (!isSuccessful(transaction as GenLayerTransaction)) {
    return { ...base, succeeded: false, reason: "genlayer-js isSuccessful() rejected this transaction." };
  }
  if (requireFinalized && !finalized) {
    return {
      ...base,
      succeeded: false,
      reason: "Transaction is ACCEPTED but not FINALIZED; it can still be appealed. Wait for finalization.",
    };
  }
  return { ...base, succeeded: true, reason: null };
}

export class MonocleTransactionError extends Error {
  constructor(
    message: string,
    public readonly outcome: TransactionOutcome,
    public readonly transaction: unknown,
  ) {
    super(message);
    this.name = "MonocleTransactionError";
  }
}

/** Minimal client surface needed here (keeps this module test-friendly). */
export interface ReceiptClient {
  waitForTransactionReceipt(args: {
    hash: TransactionHash;
    waitUntil?: "decided" | "finalized";
    interval?: number;
    retries?: number;
    fullTransaction?: boolean;
  }): Promise<GenLayerTransaction>;
}

export interface WaitOptions {
  interval?: number;
  retries?: number;
}

/** Wait for FINALIZED and assert a genuine success. Throws otherwise. */
export async function waitFinalized(
  client: ReceiptClient,
  hash: TransactionHash,
  { interval = 5000, retries = 360 }: WaitOptions = {},
): Promise<GenLayerTransaction> {
  const tx = await client.waitForTransactionReceipt({ hash, waitUntil: "finalized", interval, retries, fullTransaction: true });
  const outcome = describeTransactionOutcome(tx, { requireFinalized: true });
  if (!outcome.succeeded) {
    throw new MonocleTransactionError(outcome.reason ?? "Transaction failed.", outcome, tx);
  }
  return tx;
}

/** Wait for a decision (ACCEPTED). Only for writes nobody else acts on. */
export async function waitDecided(
  client: ReceiptClient,
  hash: TransactionHash,
  { interval = 2000, retries = 300 }: WaitOptions = {},
): Promise<GenLayerTransaction> {
  const tx = await client.waitForTransactionReceipt({ hash, waitUntil: "decided", interval, retries, fullTransaction: true });
  const outcome = describeTransactionOutcome(tx, { requireFinalized: false });
  if (!outcome.succeeded) {
    throw new MonocleTransactionError(outcome.reason ?? "Transaction failed.", outcome, tx);
  }
  return tx;
}
