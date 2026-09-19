/**
 * Typed wrappers for every Monocle method. Writes wait for FINALIZED by
 * default and throw MonocleTransactionError on anything that is not a
 * genuine FINISHED_WITH_RETURN (see finality.ts).
 */
import { read, writeAndWait, type MonocleClient, type WriteOptions } from "./client.js";
import { HEAVY_CONSENSUS_ROTATIONS } from "./networks.js";
import type {
  Address,
  ClaimScore,
  EvidenceItem,
  FetchReportItem,
  Interpretation,
  LiveInterpretation,
  MonocleInfo,
  Page,
  Reputation,
  RoundFinality,
  RoundInfo,
  Rollup,
  SourceRecord,
  StructuredClaims,
  VaultState,
} from "./types.js";

export type SourceRole = "primary" | "corroborating" | "contradicting";

export class MonocleCalls {
  constructor(
    private readonly client: MonocleClient,
    public readonly address: Address,
  ) {}

  // ------------------------------------------------------------ writes

  addSource(url: string, role: SourceRole, bond: bigint, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "add_source", [url, role], { ...opts, value: bond });
  }

  claimSourceBond(url: string, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "claim_source_bond", [url], opts);
  }

  submitInterpretation(content: string, claims: StructuredClaims, bond: bigint, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "submit_interpretation", [content, JSON.stringify(claims)], {
      ...opts,
      value: bond,
    });
  }

  backInterpretation(interpretationId: string, amount: bigint, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "back_interpretation", [interpretationId], { ...opts, value: amount });
  }

  /** Heavy: every validator re-fetches every source and re-reasons. */
  adjudicate(opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "adjudicate", [], {
      consensusMaxRotations: HEAVY_CONSENSUS_ROTATIONS,
      ...opts,
    });
  }

  challenge(round: string, alternativeInterpretationId: string, bond: bigint, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "challenge", [round, alternativeInterpretationId], {
      ...opts,
      value: bond,
    });
  }

  /** Heavy: one pairwise nondet re-evaluation. */
  resolveChallenge(round: string, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "resolve_challenge", [round], {
      consensusMaxRotations: HEAVY_CONSENSUS_ROTATIONS,
      ...opts,
    });
  }

  finalize(round: string, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "finalize", [round], opts);
  }

  settle(round: string, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "settle", [round], opts);
  }

  claim(round: string, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "claim", [round], opts);
  }

  cancelRound(round: string, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "cancel_round", [round], opts);
  }

  closeMonocle(opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "close_monocle", [], opts);
  }

  cancelClose(opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "cancel_close", [], opts);
  }

  finalizeClose(opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "finalize_close", [], opts);
  }

  flushResidual(opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "flush_residual", [], opts);
  }

  // ------------------------------------------------------------ views

  getInfo() {
    return read<MonocleInfo>(this.client, this.address, "get_monocle_info");
  }

  /**
   * The read agents act on. Money-moving agents MUST check
   * `finality === "final"` and should read with a finalized-state client.
   */
  getLiveInterpretation() {
    return read<LiveInterpretation>(this.client, this.address, "get_live_interpretation");
  }

  getPendingInterpretation() {
    return read<Record<string, unknown>>(this.client, this.address, "get_pending_interpretation");
  }

  getRoundInfo(round: string) {
    return read<RoundInfo>(this.client, this.address, "get_round_info", [round]);
  }

  getRoundInterpretations(round: string) {
    return read<Interpretation[]>(this.client, this.address, "get_round_interpretations", [round]);
  }

  getInterpretation(id: string) {
    return read<Interpretation>(this.client, this.address, "get_interpretation", [id]);
  }

  getSources() {
    return read<SourceRecord[]>(this.client, this.address, "get_sources");
  }

  getAdjudicationLog(offset: number, limit: number) {
    return read<Page<Record<string, string>>>(this.client, this.address, "get_adjudication_log", [offset, limit]);
  }

  getClaimable(round: string, account: Address) {
    return read<string>(this.client, this.address, "get_claimable", [round, account]);
  }

  isClaimed(round: string, account: Address) {
    return read<boolean>(this.client, this.address, "is_claimed", [round, account]);
  }

  getBacking(round: string, interpretationId: string, account: Address) {
    return read<string>(this.client, this.address, "get_backing", [round, interpretationId, account]);
  }

  getEvidenceSnapshot(round: string) {
    return read<{ round: string; evidence_hash: string; evidence_snapshot: EvidenceItem[]; fetch_report: FetchReportItem[]; evaluated_at: string }>(
      this.client,
      this.address,
      "get_evidence_snapshot",
      [round],
    );
  }

  getClaimScores(round: string) {
    return read<{ round: string; claim_scores: ClaimScore[]; rollups: Rollup[]; ranking: { id: string; score: string }[] }>(
      this.client,
      this.address,
      "get_claim_scores",
      [round],
    );
  }

  getReputation(account: Address) {
    return read<Reputation>(this.client, this.address, "get_reputation", [account]);
  }

  getCurrentRound() {
    return read<string>(this.client, this.address, "get_current_round");
  }

  getFinality(round: string) {
    return read<{ round: string; status: string; finality: RoundFinality }>(this.client, this.address, "get_finality", [round]);
  }

  getRoundOutcome(round: string) {
    return read<Record<string, unknown>>(this.client, this.address, "get_round_outcome", [round]);
  }

  getVaultState() {
    return read<VaultState>(this.client, this.address, "get_vault_state");
  }

  // ------------------------------------------------------------ helpers

  /** Resolve the output an agent may act on, or null if none is FINAL. */
  async getFinalOutputOrNull() {
    const live = await this.getLiveInterpretation();
    return live.has_live && live.finality === "final" ? live : null;
  }
}
