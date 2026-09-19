# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

"""
MonocleReputation -- cross-Monocle reputation aggregator. New in MONOCLE.

Pull-based on purpose. It performs only synchronous cross-contract VIEW
reads (factory.is_registered, monocle.get_round_outcome) and never relies
on a Monocle writing into it: v0.6 cross-contract writes are asynchronous
internal messages, and a Monocle must never be able to block its own
settlement on this contract. Anyone may call record_round() for any FINAL
round of any factory-registered Monocle; each (monocle, round) is recorded
at most once.

Reputation is an OUTPUT for other agents. It is never read by Monocle
adjudication, which stays stake-blind and author-blind.
"""

import json
from datetime import datetime, timezone

import genlayer as gl
from genlayer.storage import DynArray, TreeMap
from genlayer.types import Address, u256

MAX_PAGE_LIMIT = 100

_EMPTY = {
    "decided_wins": 0,
    "decided_losses": 0,
    "inconclusive_participations": 0,
    "challenges_won": 0,
    "challenges_lost": 0,
    "rounds_recorded": 0,
    "last_finalized_at": "0",
}


def _consensus_now() -> int:
    """Unix seconds of the transaction's consensus datetime (the same for
    every validator), never the node's wall clock. Accepts a trailing "Z"
    and treats a naive timestamp as UTC."""
    stamp = str(gl.message.raw["datetime"]).strip()
    if stamp.endswith(("Z", "z")):
        stamp = stamp[:-1] + "+00:00"
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def _normalize_address(addr) -> str:
    text = addr.as_hex if isinstance(addr, Address) else addr
    return text.strip().lower() if isinstance(text, str) else ""


class MonocleReputation(gl.contract.Contract):
    factory: Address
    # normalized author address -> JSON reputation record
    records: TreeMap[str, str]
    # "{monocle}:{round}" -> JSON summary of what was applied
    recorded_rounds: TreeMap[str, str]
    recorded_keys: DynArray[str]
    total_recorded: u256

    def __init__(self):
        # Deployed by MonocleFactory; the factory is the registry of truth.
        self.factory = gl.message.sender_address
        self.total_recorded = u256(0)

    def _bump(self, addr_key: str, field: str, finalized_at: str) -> None:
        if not addr_key:
            return
        rec = json.loads(self.records.get(addr_key, json.dumps(_EMPTY)))
        rec[field] = int(rec.get(field, 0)) + 1
        rec["rounds_recorded"] = int(rec.get("rounds_recorded", 0))
        if int(finalized_at or "0") > int(rec.get("last_finalized_at", "0")):
            rec["last_finalized_at"] = str(finalized_at)
        self.records[addr_key] = json.dumps(rec)

    def _touch(self, addr_key: str) -> None:
        if not addr_key:
            return
        rec = json.loads(self.records.get(addr_key, json.dumps(_EMPTY)))
        rec["rounds_recorded"] = int(rec.get("rounds_recorded", 0)) + 1
        self.records[addr_key] = json.dumps(rec)

    @gl.public.write
    def record_round(self, monocle_address: str, round: str) -> dict:
        monocle_key = _normalize_address(monocle_address)
        key = f"{monocle_key}:{round}"
        if self.recorded_rounds.get(key, "") != "":
            raise gl.vm.UserError("Round already recorded.")
        registered = gl.contract.get_at(self.factory).view().is_registered(monocle_key)
        if registered is not True:
            raise gl.vm.UserError("Monocle is not registered with this factory.")
        outcome = gl.contract.get_at(Address(monocle_key)).view().get_round_outcome(round)
        if not isinstance(outcome, dict) or outcome.get("final") is not True:
            raise gl.vm.UserError("Round is not final yet; only FINAL outcomes are recorded.")

        status = str(outcome.get("status", ""))
        finalized_at = str(outcome.get("finalized_at", "0"))
        authors = outcome.get("authors", [])
        if not isinstance(authors, list):
            authors = []
        applied = {"status": status, "wins": [], "losses": [], "inconclusive": [], "challenge": ""}

        if status in ("finalized", "settled"):
            winner_id = str(outcome.get("winner_id", ""))
            for entry in authors:
                author = _normalize_address(entry.get("author", ""))
                if entry.get("id") == winner_id:
                    self._bump(author, "decided_wins", finalized_at)
                    applied["wins"].append(author)
                else:
                    self._bump(author, "decided_losses", finalized_at)
                    applied["losses"].append(author)
                self._touch(author)
            challenger = _normalize_address(outcome.get("challenger", ""))
            challenge_outcome = str(outcome.get("challenge_outcome", ""))
            if challenger and challenge_outcome == "upheld":
                self._bump(challenger, "challenges_won", finalized_at)
                applied["challenge"] = "won"
            elif challenger and challenge_outcome == "rejected":
                self._bump(challenger, "challenges_lost", finalized_at)
                applied["challenge"] = "lost"
        elif status in ("inconclusive", "unchanged"):
            now = str(_consensus_now())
            for entry in authors:
                author = _normalize_address(entry.get("author", ""))
                self._bump(author, "inconclusive_participations", now)
                self._touch(author)
                applied["inconclusive"].append(author)
        # "cancelled" rounds are recorded (so they are not re-processed) but
        # carry no reputation signal.

        self.recorded_rounds[key] = json.dumps(applied)
        self.recorded_keys.append(key)
        self.total_recorded = u256(int(self.total_recorded) + 1)
        return applied

    @gl.public.view
    def get_reputation(self, address: str) -> dict:
        return json.loads(self.records.get(_normalize_address(address), json.dumps(_EMPTY)))

    @gl.public.view
    def is_recorded(self, monocle_address: str, round: str) -> bool:
        return self.recorded_rounds.get(f"{_normalize_address(monocle_address)}:{round}", "") != ""

    @gl.public.view
    def get_recorded_round(self, monocle_address: str, round: str) -> dict:
        raw = self.recorded_rounds.get(f"{_normalize_address(monocle_address)}:{round}", "")
        if not raw:
            raise gl.vm.UserError("Round not recorded.")
        return json.loads(raw)

    @gl.public.view
    def get_recorded_page(self, offset: int, limit: int) -> dict:
        total = len(self.recorded_keys)
        if offset < 0 or limit <= 0 or offset >= total:
            return {"total": total, "offset": offset, "keys": []}
        end = min(total, offset + min(limit, MAX_PAGE_LIMIT))
        return {"total": total, "offset": offset, "keys": [self.recorded_keys[i] for i in range(offset, end)]}

    @gl.public.view
    def get_factory(self) -> str:
        return self.factory.as_hex
