"""
MONOCLE Python client.

* Default network: Studio Next = genlayer-py's ``studio_devnet`` preset
  (chain 61997, canonical RPC https://studio-dev.genlayer.com/api).
* Reads need no key.
* Every write: SDK fee estimation for that exact call, submit, wait for
  FINALIZED, then a strict outcome check -- a FINISHED_WITH_ERROR revert is
  a failure even when consensus accepted it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from genlayer_py import create_account, create_client
from genlayer_py.chains import localnet, studio_devnet
from genlayer_py.transactions import is_successful

STUDIO_NEXT_CHAIN_ID = 61997
STUDIO_NEXT_RPC = "https://studio-dev.genlayer.com/api"
# adjudicate / resolve_challenge: multi-source fetch + multi-stage LLM verdict
# re-run by every validator; allow more leader rotations than the default 3.
HEAVY_CONSENSUS_ROTATIONS = 6

NETWORKS = {"studio-next": studio_devnet, "localnet": localnet}


def _chain(network: str):
    if network not in NETWORKS:
        raise ValueError(f"Unknown network {network!r}; valid: {sorted(NETWORKS)}")
    return NETWORKS[network]


def create_read_client(network: str = "studio-next", endpoint: Optional[str] = None):
    return create_client(chain=_chain(network), endpoint=endpoint)


def create_write_client(private_key: str, network: str = "studio-next", endpoint: Optional[str] = None):
    return create_client(chain=_chain(network), endpoint=endpoint, account=create_account(private_key))


@dataclass
class TransactionOutcome:
    succeeded: bool
    finalized: bool
    execution_result: str
    reason: Optional[str]


def _get(tx: Any, key: str, default=None):
    if isinstance(tx, dict):
        return tx.get(key, default)
    return getattr(tx, key, default)


def describe_transaction_outcome(tx: Any, require_finalized: bool = True) -> TransactionOutcome:
    lifecycle = _get(tx, "lifecycle") or {}
    state = lifecycle.get("state") if isinstance(lifecycle, dict) else None
    status_name = str(_get(tx, "status_name", "") or "")
    finalized = state == "finalized" or status_name == "FINALIZED"
    result = _get(tx, "tx_execution_result_name", _get(tx, "tx_execution_result"))
    result_name = str(getattr(result, "value", result) or "missing")

    if result_name == "FINISHED_WITH_ERROR":
        return TransactionOutcome(False, finalized, result_name, "Contract reverted (FINISHED_WITH_ERROR); nothing changed.")
    if not is_successful(tx):
        return TransactionOutcome(False, finalized, result_name, f"Not a confirmed success (lifecycle={state}, result={result_name}).")
    if require_finalized and not finalized:
        return TransactionOutcome(False, finalized, result_name, "Accepted but not FINALIZED; it can still be appealed.")
    return TransactionOutcome(True, finalized, result_name, None)


class MonocleTransactionError(RuntimeError):
    def __init__(self, outcome: TransactionOutcome, tx: Any):
        super().__init__(outcome.reason or "transaction failed")
        self.outcome = outcome
        self.transaction = tx


class _Base:
    def __init__(self, client, address: str):
        self.client = client
        self.address = address

    def _read(self, fn: str, *args):
        return self.client.read_contract(address=self.address, function_name=fn, args=list(args))

    def _write(self, fn: str, *args, value: int = 0, rotations: Optional[int] = None, require_finalized: bool = True):
        fees = self.client.estimate_transaction_fees_for_write(
            address=self.address, function_name=fn, args=list(args), value=value
        )
        tx_hash = self.client.write_contract(
            address=self.address,
            function_name=fn,
            args=list(args),
            value=value,
            consensus_max_rotations=rotations,
            fees=fees,
        )
        tx = self.client.wait_for_transaction_receipt(
            transaction_hash=tx_hash,
            wait_until="finalized" if require_finalized else "decided",
            full_transaction=True,
        )
        outcome = describe_transaction_outcome(tx, require_finalized=require_finalized)
        if not outcome.succeeded:
            raise MonocleTransactionError(outcome, tx)
        return tx


class MonocleClient(_Base):
    # -- writes -------------------------------------------------------------
    def add_source(self, url: str, role: str, bond: int):
        return self._write("add_source", url, role, value=bond)

    def claim_source_bond(self, url: str):
        return self._write("claim_source_bond", url)

    def submit_interpretation(self, content: str, claims: dict, bond: int):
        return self._write("submit_interpretation", content, json.dumps(claims), value=bond)

    def back_interpretation(self, interpretation_id: str, amount: int):
        return self._write("back_interpretation", interpretation_id, value=amount)

    def adjudicate(self):
        return self._write("adjudicate", rotations=HEAVY_CONSENSUS_ROTATIONS)

    def challenge(self, round: str, alternative_id: str, bond: int):
        return self._write("challenge", round, alternative_id, value=bond)

    def resolve_challenge(self, round: str):
        return self._write("resolve_challenge", round, rotations=HEAVY_CONSENSUS_ROTATIONS)

    def finalize(self, round: str):
        return self._write("finalize", round)

    def settle(self, round: str):
        return self._write("settle", round)

    def claim(self, round: str):
        return self._write("claim", round)

    def cancel_round(self, round: str):
        return self._write("cancel_round", round)

    def close_monocle(self):
        return self._write("close_monocle")

    def cancel_close(self):
        return self._write("cancel_close")

    def finalize_close(self):
        return self._write("finalize_close")

    def flush_residual(self):
        return self._write("flush_residual")

    # -- views --------------------------------------------------------------
    def info(self):
        return self._read("get_monocle_info")

    def live(self):
        return self._read("get_live_interpretation")

    def final_output_or_none(self):
        """The only output a money-moving agent may act on."""
        live = self.live()
        return live if live.get("has_live") and live.get("finality") == "final" else None

    def pending(self):
        return self._read("get_pending_interpretation")

    def round_info(self, round: str):
        return self._read("get_round_info", round)

    def round_interpretations(self, round: str):
        return self._read("get_round_interpretations", round)

    def interpretation(self, interpretation_id: str):
        return self._read("get_interpretation", interpretation_id)

    def sources(self):
        return self._read("get_sources")

    def adjudication_log(self, offset: int = 0, limit: int = 50):
        return self._read("get_adjudication_log", offset, limit)

    def claimable(self, round: str, address: str):
        return self._read("get_claimable", round, address)

    def evidence_snapshot(self, round: str):
        return self._read("get_evidence_snapshot", round)

    def claim_scores(self, round: str):
        return self._read("get_claim_scores", round)

    def reputation(self, address: str):
        return self._read("get_reputation", address)

    def current_round(self):
        return self._read("get_current_round")

    def finality(self, round: str):
        return self._read("get_finality", round)

    def vault_state(self):
        return self._read("get_vault_state")


class FactoryClient(_Base):
    def create_monocle(self, sources, interpretation_type, title, description="", schema=None, value=None):
        """Creates a Monocle and returns its address (resolved from the
        append-only registry at the index known before the write)."""
        stake = int(self._read("get_creation_stake")) if value is None else value
        index = int(self._read("get_monocles_count"))
        self._write(
            "create_monocle",
            list(sources),
            interpretation_type,
            title,
            description,
            json.dumps(schema) if schema else "",
            value=stake,
        )
        return self._read("get_monocles_page", index, 1)["addresses"][0]

    def withdraw_fees(self):
        return self._write("withdraw_fees")

    def monocles_page(self, offset: int = 0, limit: int = 50):
        return self._read("get_monocles_page", offset, limit)

    def meta(self, monocle: str):
        return self._read("get_monocle_meta", monocle)

    def reputation_address(self):
        return self._read("get_reputation_address")


class ReputationClient(_Base):
    def record_round(self, monocle: str, round: str):
        return self._write("record_round", monocle, round)

    def reputation(self, address: str):
        return self._read("get_reputation", address)
