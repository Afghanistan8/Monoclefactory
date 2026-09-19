import { read, writeAndWait, type MonocleClient, type WriteOptions } from "./client.js";
import type { Address, MonocleMeta, Page, Reputation } from "./types.js";

export interface CreateMonocleArgs {
  sources: string[];
  interpretationType: string;
  title: string;
  description?: string;
  /** JSON object: required field name -> short description. "" for none. */
  schema?: Record<string, string>;
  /** Defaults to the factory's current creation stake. Excess is refunded. */
  value?: bigint;
}

export class FactoryCalls {
  constructor(
    private readonly client: MonocleClient,
    public readonly address: Address,
  ) {}

  /**
   * Creates a Monocle and resolves its address from the append-only
   * registry (the index is known before the write), after FINALIZED.
   */
  async createMonocle(args: CreateMonocleArgs, opts: WriteOptions = {}) {
    const value = args.value ?? BigInt(await this.getCreationStake());
    const index = await this.getMonoclesCount();
    const result = await writeAndWait(
      this.client,
      this.address,
      "create_monocle",
      [args.sources, args.interpretationType, args.title, args.description ?? "", args.schema ? JSON.stringify(args.schema) : ""],
      { ...opts, value },
    );
    const page = await this.getMonoclesPage(index, 1);
    const monocle = page.addresses?.[0];
    if (!monocle) throw new Error("create_monocle finalized but no registry entry appeared at the expected index.");
    return { ...result, monocle: monocle as Address };
  }

  withdrawFees(opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "withdraw_fees", [], opts);
  }

  setCreationStake(stake: bigint, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "set_creation_stake", [stake], opts);
  }

  getOwner() {
    return read<Address>(this.client, this.address, "get_owner");
  }
  getCreationStake() {
    return read<string>(this.client, this.address, "get_creation_stake");
  }
  getBondConfig() {
    return read<Record<string, string>>(this.client, this.address, "get_bond_config");
  }
  getCollectedFees() {
    return read<string>(this.client, this.address, "get_collected_fees");
  }
  getResidualFees() {
    return read<string>(this.client, this.address, "get_residual_fees");
  }
  getMonocles() {
    return read<Address[]>(this.client, this.address, "get_monocles");
  }
  async getMonoclesCount() {
    return Number(await read<number | bigint>(this.client, this.address, "get_monocles_count"));
  }
  getMonoclesPage(offset: number, limit: number) {
    return read<Page<Address>>(this.client, this.address, "get_monocles_page", [offset, limit]);
  }
  getMonocleMeta(monocle: Address) {
    return read<MonocleMeta>(this.client, this.address, "get_monocle_meta", [monocle]);
  }
  isRegistered(monocle: Address) {
    return read<boolean>(this.client, this.address, "is_registered", [monocle]);
  }
  getMonoclesByCreator(creator: Address) {
    return read<Address[]>(this.client, this.address, "get_monocles_by_creator", [creator]);
  }
  getMonoclesByType(interpretationType: string) {
    return read<Address[]>(this.client, this.address, "get_monocles_by_type", [interpretationType]);
  }
  getReputationAddress() {
    return read<Address | "">(this.client, this.address, "get_reputation_address");
  }
  getVaultModel() {
    return read<{ model: string; detail: string }>(this.client, this.address, "get_vault_model");
  }
}

export class ReputationCalls {
  constructor(
    private readonly client: MonocleClient,
    public readonly address: Address,
  ) {}

  /** Permissionless pull-sync of one FINAL round. */
  recordRound(monocle: Address, round: string, opts: WriteOptions = {}) {
    return writeAndWait(this.client, this.address, "record_round", [monocle, round], opts);
  }
  getReputation(account: Address) {
    return read<Reputation>(this.client, this.address, "get_reputation", [account]);
  }
  isRecorded(monocle: Address, round: string) {
    return read<boolean>(this.client, this.address, "is_recorded", [monocle, round]);
  }
  getRecordedPage(offset: number, limit: number) {
    return read<Page<string>>(this.client, this.address, "get_recorded_page", [offset, limit]);
  }
}
