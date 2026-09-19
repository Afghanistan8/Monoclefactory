/**
 * View shapes returned by MONOCLE contracts. Every number is a decimal
 * STRING (GenVM calldata has no float type): parse with BigInt() for wei and
 * Number() for scores/confidence.
 */

export type Address = `0x${string}`;

export type MonocleStatus = "active" | "closing" | "closed";

export type RoundStatus =
  | "open"
  | "adjudicating"
  | "decided_pending"
  | "challenged"
  | "resolving"
  | "finalized"
  | "settled"
  | "inconclusive"
  | "cancelled"
  | "unchanged";

/** Contract-level finality of a round's outcome. */
export type RoundFinality = "none" | "pending" | "final" | "refund";

/** Finality of get_live_interpretation().interpretation. */
export type LiveFinality = "none" | "pending" | "final";

export type Decision = "decided" | "no_evidence" | "unchanged" | "invalid_verdict" | "insufficient_corroboration";

export type ChallengeOutcome = "open" | "upheld" | "rejected" | "unresolved" | "expired";

export interface Claim {
  id: string;
  statement: string;
  polarity: "affirm" | "deny";
  entities: string[];
  core: boolean;
}

export interface Interpretation {
  id: string;
  round: string;
  author: Address;
  content: string;
  claims: Claim[];
  fields: Record<string, unknown>;
  stance: "assertion" | "refutation";
  content_hash: string;
  total_stake: string;
  backer_count: number;
  created_at: string;
}

export interface EvidenceItem {
  url: string;
  role: "primary" | "corroborating" | "contradicting";
  /** Sliced from the REAL fetched page in contract code, never from the model. */
  excerpt: string;
}

export interface FetchReportItem {
  url: string;
  ok: boolean;
  content_hash: string;
}

export interface ClaimScore {
  interpretation_id: string;
  claim_id: string;
  statement: string;
  core: boolean;
  verdict: "supported" | "contradicted" | "insufficient";
  support_source_urls: string[];
  note: string;
}

export interface Rollup {
  id: string;
  claims: number;
  supported: number;
  contradicted: number;
  corroborating_sources: number;
  rollup_bps: number;
  disqualified: boolean;
}

export interface AdjudicationRecord {
  decision: Decision;
  outcome: RoundStatus;
  reason: string;
  confidence: string;
  composite_score: string;
  reasoning: string;
  evidence_snapshot: EvidenceItem[];
  evidence_hash: string;
  claim_scores: ClaimScore[];
  rollups: Rollup[];
  ranking: { id: string; score: string }[];
  fetch_report: FetchReportItem[];
  sources_checked: string[];
  sources_fetched_ok: number;
  evaluated_at: string;
  winner_id?: string;
  final_winner_id?: string;
  challenge_outcome?: ChallengeOutcome | "";
}

export interface ChallengeRecord {
  challenger: Address;
  challenger_key: string;
  alternative_id: string;
  original_winner: string;
  bond: string;
  challenged_at: string;
  outcome: ChallengeOutcome;
  preferred_id?: string;
  confidence?: string;
  reasoning?: string;
  reward?: string;
  payout?: string;
  resolved_at?: string;
}

export interface PendingSummary {
  round: string;
  status: RoundStatus;
  winner_id: string;
  decided_at: string;
  challenge_deadline: string;
  reasoning: AdjudicationRecord;
}

export interface LiveInterpretation {
  has_live: boolean;
  finality: LiveFinality;
  interpretation: Interpretation | Record<string, never>;
  reasoning: AdjudicationRecord | Record<string, never>;
  pending: PendingSummary | Record<string, never>;
}

export interface RoundInfo {
  round: string;
  status: RoundStatus;
  finality: RoundFinality;
  opened_at: string;
  pool: string;
  bonus: string;
  pot: string;
  winner_id: string;
  winner_total: string;
  pending_winner: string;
  decided_at: string;
  challenge_deadline: string;
  finalized_at: string;
  settled_at: string;
  paid_winners: string;
  reasoning: AdjudicationRecord | Record<string, never>;
  challenge: ChallengeRecord | Record<string, never>;
  interpretation_ids: string[];
}

export interface MonocleInfo {
  monocle_id: string;
  factory_address: Address;
  deployed_by_factory: boolean;
  creator: Address;
  sources: string[];
  interpretation_type: string;
  title: string;
  description: string;
  schema: Record<string, string>;
  status: MonocleStatus;
  close_requested_at: string;
  close_executable_at: string;
  current_round: string;
  current_round_status: RoundStatus;
  live_interpretation_id: string;
  live_round: string;
  live_since: string;
  total_stake_all_time: string;
  last_adjudicated: string;
  created_at: string;
  interpretation_count: string;
  min_interpretation_bond: string;
  min_source_bond: string;
  min_challenge_bond: string;
  /** Seconds a decided verdict stays challengeable (deployment-wide, set by the factory). */
  challenge_window_seconds: string;
  constants: Record<string, string | number>;
}

export interface SourceRecord {
  url: string;
  role: "primary" | "corroborating" | "contradicting";
  added_by: Address;
  added_at: string;
  add_bond: string;
  bond_status: "none" | "locked" | "refundable" | "refunded" | "forfeited";
  misses: number;
  fetch_ok_count: number;
  last_fetch_ok: boolean;
  last_content_hash: string;
  last_fetched_at: string;
}

export interface Reputation {
  decided_wins: number;
  decided_losses: number;
  inconclusive_participations: number;
  challenges_won: number;
  challenges_lost: number;
  last_finalized_at: string;
  rounds_recorded?: number;
}

export interface VaultState {
  tracked: string;
  bucket_sum: string;
  native_balance: string;
  conserved: boolean;
  solvent: boolean;
  buckets: Record<string, string>;
}

export interface MonocleMeta {
  address: Address;
  index: number;
  sources: string[];
  interpretation_type: string;
  title: string;
  description: string;
  schema_json: string;
  creator: Address;
  created_at: string;
  stake_required: string;
  stake_paid: string;
  excess_refunded: string;
  min_interpretation_bond: string;
  min_source_bond: string;
  min_challenge_bond: string;
  challenge_window_seconds: string;
}

export interface Page<T> {
  total: number;
  offset: number;
  addresses?: T[];
  entries?: T[];
  keys?: T[];
}

/** Structured claims accepted by submit_interpretation. */
export interface StructuredClaims {
  claims?: (string | { statement: string; polarity?: "affirm" | "deny"; entities?: string[]; core?: boolean })[];
  stance?: "assertion" | "refutation";
  [schemaField: string]: unknown;
}
