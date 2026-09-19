# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

"""
MonocleFactory -- registry and on-chain factory for MONOCLE engines.

Properties:
  * refunds any value sent above creation_stake to the creator, and counts
    exactly creation_stake into collected_fees;
  * passes the real creator to each Monocle (otherwise the factory itself
    would be the creator of every child);
  * deploys one MonocleReputation aggregator at construction;
  * accepts residual flushes from closed Monocles via receive_residual().

Registry metadata is creation-time only. Live round / live output state is
never mirrored here -- read it from the Monocle instance.

Owner privilege: fee withdrawal and setting the creation stake for FUTURE
Monocles. Nothing else. There is no pause switch.
"""

import json
from datetime import datetime, timezone

import genlayer as gl
from genlayer.storage import DynArray, TreeMap
from genlayer.types import Address, u256

MIN_SOURCES = 2
MAX_SOURCES = 8
MAX_URL_LEN = 500
MAX_TITLE_LEN = 140
MAX_DESC_LEN = 1000
MAX_TYPE_LEN = 40
MAX_SCHEMA_RAW_LEN = 2000
MAX_PAGE_LIMIT = 100

# gl.contract.deploy derives CREATE2 addresses from (deployer, salt, chain)
# only -- not from code -- so salts must never collide across child kinds.
REPUTATION_SALT = 1
MONOCLE_SALT_OFFSET = 2
MIN_CHALLENGE_WINDOW_SECONDS = 60
MAX_CHALLENGE_WINDOW_SECONDS = 7 * 86400


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


def _is_http_url(url) -> bool:
    if not isinstance(url, str):
        return False
    u = url.strip()
    if not u or len(u) > MAX_URL_LEN:
        return False
    lowered = u.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


class MonocleFactory(gl.contract.Contract):
    monocle_code: str
    creation_stake: u256
    owner: Address
    min_interpretation_bond: u256
    min_source_bond: u256
    min_challenge_bond: u256
    # Passed to every Monocle this factory creates.
    challenge_window_seconds: u256
    # Exactly creation_stake per create_monocle, never the overpayment.
    collected_fees: u256
    # Value received via receive_residual() (Monocle.flush_residual).
    residual_fees: u256
    reputation_address: str
    monocle_addresses: DynArray[str]
    # normalized address -> JSON creation-time metadata
    monocle_meta: TreeMap[str, str]

    def __init__(
        self,
        monocle_code: str,
        reputation_code: str,
        creation_stake: int,
        min_interpretation_bond: int,
        min_source_bond: int,
        min_challenge_bond: int,
        challenge_window_seconds: int,
    ):
        if not monocle_code:
            raise gl.vm.UserError("Missing Monocle contract source code.")
        if not isinstance(creation_stake, int) or creation_stake < 0:
            raise gl.vm.UserError("creation_stake must be a non-negative integer (wei).")
        for name, value in (
            ("min_interpretation_bond", min_interpretation_bond),
            ("min_source_bond", min_source_bond),
            ("min_challenge_bond", min_challenge_bond),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise gl.vm.UserError(f"{name} must be a positive integer (wei).")
        if (
            not isinstance(challenge_window_seconds, int)
            or isinstance(challenge_window_seconds, bool)
            or not MIN_CHALLENGE_WINDOW_SECONDS <= challenge_window_seconds <= MAX_CHALLENGE_WINDOW_SECONDS
        ):
            raise gl.vm.UserError(
                f"challenge_window_seconds must be between {MIN_CHALLENGE_WINDOW_SECONDS} and "
                f"{MAX_CHALLENGE_WINDOW_SECONDS}."
            )
        self.monocle_code = monocle_code
        self.creation_stake = u256(creation_stake)
        self.owner = gl.message.sender_address
        self.min_interpretation_bond = u256(min_interpretation_bond)
        self.min_source_bond = u256(min_source_bond)
        self.min_challenge_bond = u256(min_challenge_bond)
        self.challenge_window_seconds = u256(challenge_window_seconds)
        self.collected_fees = u256(0)
        self.residual_fees = u256(0)
        self.reputation_address = ""
        if reputation_code:
            addr = gl.contract.deploy(
                code=reputation_code.encode("utf-8"),
                args=[],
                salt_nonce=u256(REPUTATION_SALT),
            )
            self.reputation_address = addr.as_hex if addr is not None else ""

    @gl.public.write.payable
    def create_monocle(
        self,
        sources: list[str],
        interpretation_type: str,
        title: str,
        description: str,
        schema_json: str,
    ) -> str:
        paid = int(gl.message.value)
        required = int(self.creation_stake)
        if paid < required:
            raise gl.vm.UserError(f"Creation stake too low: sent {paid}, requires {required}")
        if not isinstance(sources, list) or len(sources) < MIN_SOURCES:
            raise gl.vm.UserError(f"At least {MIN_SOURCES} sources are required for evidentiary corroboration.")
        if len(sources) > MAX_SOURCES:
            raise gl.vm.UserError(f"At most {MAX_SOURCES} sources are allowed.")
        if not all(_is_http_url(u) for u in sources):
            raise gl.vm.UserError(f"Sources must be http(s) URLs, each at most {MAX_URL_LEN} characters.")
        if not interpretation_type or len(interpretation_type) > MAX_TYPE_LEN:
            raise gl.vm.UserError(f"interpretation_type is required and must be at most {MAX_TYPE_LEN} characters.")
        if not title or len(title) > MAX_TITLE_LEN:
            raise gl.vm.UserError(f"Title is required and must be at most {MAX_TITLE_LEN} characters.")
        if len(description) > MAX_DESC_LEN:
            raise gl.vm.UserError(f"Description exceeds {MAX_DESC_LEN} characters.")
        if len(schema_json) > MAX_SCHEMA_RAW_LEN:
            raise gl.vm.UserError("schema_json exceeds max length.")

        creator = gl.message.sender_address
        registered = len(self.monocle_addresses)
        address = gl.contract.deploy(
            code=self.monocle_code.encode("utf-8"),
            args=[
                [u.strip() for u in sources],
                interpretation_type,
                title,
                description,
                schema_json,
                int(self.min_interpretation_bond),
                int(self.min_source_bond),
                int(self.min_challenge_bond),
                int(self.challenge_window_seconds),
                creator.as_hex,
            ],
            salt_nonce=u256(registered + MONOCLE_SALT_OFFSET),
        )
        address_hex = address.as_hex
        self.monocle_addresses.append(address_hex)

        # Effects before interaction: account for exactly the required stake,
        # then refund the excess to the creator.
        excess = paid - required
        self.collected_fees = u256(int(self.collected_fees) + required)
        self.monocle_meta[_normalize_address(address_hex)] = json.dumps(
            {
                "address": address_hex,
                "index": registered,
                "sources": [u.strip() for u in sources],
                "interpretation_type": interpretation_type,
                "title": title,
                "description": description,
                "schema_json": schema_json,
                "creator": creator.as_hex,
                "created_at": str(_consensus_now()),
                "stake_required": str(required),
                "stake_paid": str(paid),
                "excess_refunded": str(excess),
                "min_interpretation_bond": str(int(self.min_interpretation_bond)),
                "min_source_bond": str(int(self.min_source_bond)),
                "min_challenge_bond": str(int(self.min_challenge_bond)),
                "challenge_window_seconds": str(int(self.challenge_window_seconds)),
            }
        )
        if excess > 0:
            gl.contract.get_at(creator).emit_transfer(value=u256(excess))
        return address_hex

    @gl.public.write.payable
    def receive_residual(self) -> None:
        # Residual flushes from closed Monocles (and any donation). Not
        # __receive__: this RC's schema validator rejects public dunder names.
        if int(gl.message.value) <= 0:
            raise gl.vm.UserError("receive_residual requires a positive value.")
        self.residual_fees = u256(int(self.residual_fees) + int(gl.message.value))

    @gl.public.write
    def withdraw_fees(self) -> str:
        if _normalize_address(gl.message.sender_address) != _normalize_address(self.owner):
            raise gl.vm.UserError("Only the factory owner may withdraw collected fees.")
        amount = int(self.collected_fees) + int(self.residual_fees)
        if amount == 0:
            raise gl.vm.UserError("No fees to withdraw.")
        # Effects before interaction.
        self.collected_fees = u256(0)
        self.residual_fees = u256(0)
        gl.contract.get_at(self.owner).emit_transfer(value=u256(amount))
        return str(amount)

    @gl.public.write
    def set_creation_stake(self, creation_stake: int) -> None:
        if _normalize_address(gl.message.sender_address) != _normalize_address(self.owner):
            raise gl.vm.UserError("Only the factory owner may set the creation stake.")
        if not isinstance(creation_stake, int) or isinstance(creation_stake, bool) or creation_stake < 0:
            raise gl.vm.UserError("creation_stake must be a non-negative integer (wei).")
        # Existing Monocles are unaffected: the stake is only read at creation.
        self.creation_stake = u256(creation_stake)

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    @gl.public.view
    def get_owner(self) -> str:
        return self.owner.as_hex

    @gl.public.view
    def get_creation_stake(self) -> str:
        return str(int(self.creation_stake))

    @gl.public.view
    def get_bond_config(self) -> dict:
        return {
            "min_interpretation_bond": str(int(self.min_interpretation_bond)),
            "min_source_bond": str(int(self.min_source_bond)),
            "min_challenge_bond": str(int(self.min_challenge_bond)),
            "challenge_window_seconds": str(int(self.challenge_window_seconds)),
        }

    @gl.public.view
    def get_collected_fees(self) -> str:
        return str(int(self.collected_fees))

    @gl.public.view
    def get_residual_fees(self) -> str:
        return str(int(self.residual_fees))

    @gl.public.view
    def get_monocles(self) -> list[str]:
        return list(self.monocle_addresses)

    @gl.public.view
    def get_monocles_count(self) -> int:
        return len(self.monocle_addresses)

    @gl.public.view
    def get_monocles_page(self, offset: int, limit: int) -> dict:
        total = len(self.monocle_addresses)
        if offset < 0 or limit <= 0 or offset >= total:
            return {"total": total, "offset": offset, "addresses": []}
        end = min(total, offset + min(limit, MAX_PAGE_LIMIT))
        return {
            "total": total,
            "offset": offset,
            "addresses": [self.monocle_addresses[i] for i in range(offset, end)],
        }

    @gl.public.view
    def get_monocle_meta(self, address: str) -> dict:
        raw = self.monocle_meta.get(_normalize_address(address), "")
        if not raw:
            raise gl.vm.UserError("Unknown Monocle address.")
        return json.loads(raw)

    @gl.public.view
    def is_registered(self, address: str) -> bool:
        return self.monocle_meta.get(_normalize_address(address), "") != ""

    @gl.public.view
    def get_monocles_by_creator(self, creator_address: str) -> list[str]:
        target = _normalize_address(creator_address)
        out = []
        for address_hex in self.monocle_addresses:
            meta = json.loads(self.monocle_meta.get(_normalize_address(address_hex), "{}"))
            if _normalize_address(meta.get("creator", "")) == target:
                out.append(address_hex)
        return out

    @gl.public.view
    def get_monocles_by_type(self, interpretation_type: str) -> list[str]:
        out = []
        for address_hex in self.monocle_addresses:
            meta = json.loads(self.monocle_meta.get(_normalize_address(address_hex), "{}"))
            if meta.get("interpretation_type", "") == interpretation_type:
                out.append(address_hex)
        return out

    @gl.public.view
    def get_vault_model(self) -> dict:
        return {
            "model": "in_process",
            "detail": "Each Monocle is its own custodian through an isolated MonocleVault ledger. "
            "v0.6 cross-contract writes are asynchronous messages, so a shared vault contract "
            "could not be debited synchronously inside claim(). See docs/ARCHITECTURE.md.",
        }

    @gl.public.view
    def get_reputation_address(self) -> str:
        return self.reputation_address
