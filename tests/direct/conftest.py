"""
Direct-mode fixtures and helpers for MONOCLE (gltest WASI mock).

Harness facts verified against genlayer-test 0.30.0rc2 + GenVM v0.6 RC:
  * gltest's loader unlinks a temp stdin file that is still open on Windows
    (PermissionError). Patched below; harmless on POSIX.
  * vm.warp() does not update gl.message.raw["datetime"], which contracts
    read for consensus time. warp_to()/advance() patch both.
  * Direct mode does NOT roll back storage when a call raises. Tests that
    need real transaction atomicity use run_tx(), which snapshots/reverts.
  * Value transfers and messages (emit_transfer / emit) are not executed in
    direct mode. install_recorder() hooks gl_call to capture every
    EmitInternalMessage so payout amounts can be asserted exactly.
"""

import contextlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = ROOT / "contracts"
MONOCLE_PATH = CONTRACTS_DIR / "Monocle.py"
FACTORY_PATH = CONTRACTS_DIR / "MonocleFactory.py"
REPUTATION_PATH = CONTRACTS_DIR / "MonocleReputation.py"

SOURCES = ["https://example.com/feed", "https://example.org/feed"]
# (min_interpretation_bond, min_source_bond, min_challenge_bond)
BONDS = (10, 5, 20)

ADJUDICATOR_RE = r"adjudicator for MONOCLE"
ARBITER_RE = r"challenge arbiter for MONOCLE"

# Pin the GenVM runtime so runs are deterministic and never hit the GitHub
# releases API (which rate-limits and otherwise slows every deploy).
os.environ.setdefault("GENVM_VERSION", "v0.6.0-rc5")

if sys.platform == "win32":
    # The direct loader removes its temp stdin file while it is still open,
    # which Windows refuses. The leftover temp file is harmless.
    def _tolerant_unlink(path, *args, _unlink=os.unlink, **kwargs):
        with contextlib.suppress(PermissionError):
            _unlink(path, *args, **kwargs)

    os.unlink = _tolerant_unlink


# ----------------------------------------------------------------------
# Time
# ----------------------------------------------------------------------


def _message_raw():
    mod = sys.modules.get("genlayer.message")
    return getattr(mod, "raw", None) if mod is not None else None


def now_ts(vm) -> int:
    raw = _message_raw()
    iso = raw["datetime"] if isinstance(raw, dict) and raw.get("datetime") else vm._datetime
    return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())


def warp_to(vm, ts: int) -> None:
    iso = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    vm.warp(iso)
    raw = _message_raw()
    if isinstance(raw, dict):
        raw["datetime"] = iso


def advance(vm, seconds: int) -> None:
    warp_to(vm, now_ts(vm) + seconds)


# ----------------------------------------------------------------------
# Addresses
# ----------------------------------------------------------------------


def to_hex(addr) -> str:
    """The exact checksummed hex the contract's Address.as_hex produces."""
    if hasattr(addr, "as_hex"):
        return addr.as_hex
    # Before the first deploy the GenVM SDK is not on sys.path yet; EIP-55
    # is the same checksum Address.as_hex produces.
    from eth_utils import to_checksum_address

    raw = addr if isinstance(addr, bytes) else bytes(addr)
    return to_checksum_address("0x" + raw.hex())


def key(addr) -> str:
    return to_hex(addr).lower()


# ----------------------------------------------------------------------
# gl_call recorder (transfers, messages, cross-contract view stubs)
# ----------------------------------------------------------------------


class Recorder:
    def __init__(self):
        self.messages = []
        self.deploys = []
        self.view_stubs = {}
        self.views = []

    def transfers_to(self, addr) -> int:
        target = key(addr) if not isinstance(addr, str) else addr.lower()
        return sum(m["value"] for m in self.messages if m["to"] == target and m["method"] is None)

    def total_out(self) -> int:
        return sum(m["value"] for m in self.messages)

    def clear(self):
        self.messages.clear()
        self.deploys.clear()
        self.views.clear()


def _addr_key(address) -> str:
    if hasattr(address, "as_bytes"):
        return "0x" + address.as_bytes.hex()
    if isinstance(address, bytes):
        return "0x" + address.hex()
    return str(address).lower()


def install_recorder(vm) -> Recorder:
    rec = Recorder()

    def hook(_vm, request):
        from gltest.direct.sdk_compat import import_calldata

        if "EmitInternalMessage" in request:
            msg = request["EmitInternalMessage"]
            cd = msg.get("calldata", {}) or {}
            rec.messages.append(
                {
                    "to": _addr_key(msg.get("address")),
                    "value": int(msg.get("value", 0)),
                    "method": cd.get("") if isinstance(cd, dict) else None,
                    "on": msg.get("on"),
                }
            )
            return {"ok": None}
        if "EmitInternalDeployMessage" in request:
            rec.deploys.append(request["EmitInternalDeployMessage"])
            return {"ok": None}
        if "CallContract" in request:
            call = request["CallContract"]
            target = _addr_key(call.get("address"))
            cd = call.get("calldata", {}) or {}
            method = cd.get("")
            args = cd.get("args", [])
            rec.views.append((target, method, args))
            stub = rec.view_stubs.get((target, method))
            if stub is None:
                return bytes([1]) + import_calldata().encode(f"no stub for {target}.{method}")
            result = stub(*args) if callable(stub) else stub
            return bytes([0]) + import_calldata().encode(result)
        return None

    vm._gl_call_hook = hook
    return rec


# ----------------------------------------------------------------------
# LLM capture
# ----------------------------------------------------------------------


class PromptCapture:
    def __init__(self):
        self.prompts = []
        self.response = ""


def capture_llm(vm, response: str) -> PromptCapture:
    """Answer every UNMOCKED prompt with `response` and record the prompt
    text, so tests can assert what the adjudicator was (and was not) shown."""
    cap = PromptCapture()
    cap.response = response

    def handler(data):
        cap.prompts.append(data.get("prompt", ""))
        return {"ok": cap.response}

    vm._live_llm_handler = handler
    return cap


# ----------------------------------------------------------------------
# Transaction atomicity emulation
# ----------------------------------------------------------------------


def run_tx(vm, fn, *args, **kwargs):
    """Call a contract method with GenVM's all-or-nothing semantics: if it
    raises, storage is restored to the pre-call snapshot (as a reverted
    transaction would be on a real node) and the exception re-raised."""
    snap = vm.snapshot()
    try:
        return fn(*args, **kwargs)
    except Exception:
        vm.revert(snap)
        raise


# ----------------------------------------------------------------------
# Deploy + scenario helpers
# ----------------------------------------------------------------------


def reset_contract_registry() -> None:
    """The SDK allows one Contract subclass per process; a second deploy in
    the same VM (e.g. a factory test that also loads Monocle) needs the
    registry cleared, exactly as glsim's engine does."""
    mod = sys.modules.get("genlayer.contract")
    if mod is not None and hasattr(mod, "__known_contract__"):
        setattr(mod, "__known_contract__", None)


def deploy(vm, path, *args):
    from gltest.direct import deploy_contract

    reset_contract_registry()
    return deploy_contract(path, vm, *args)


def deploy_monocle(
    vm,
    creator,
    sources=None,
    interpretation_type="market",
    title="Title",
    description="Desc",
    schema_json="",
    bonds=BONDS,
    creator_arg="",
    challenge_window=3600,
):
    vm.sender = creator
    vm.value = 0
    return deploy(
        vm,
        MONOCLE_PATH,
        list(sources if sources is not None else SOURCES),
        interpretation_type,
        title,
        description,
        schema_json,
        *bonds,
        challenge_window,
        creator_arg,
    )


def web(body: str) -> dict:
    return {"method": "GET", "status": 200, "body": body}


def mock_sources(vm, body_a="Evidence A: dominance climbed on ETF inflows.", body_b=None, sources=None):
    srcs = sources or SOURCES
    bodies = [body_a, body_b if body_b is not None else body_a.replace("Evidence A", "Evidence B")]
    for url, body in zip(srcs, bodies):
        vm.mock_web(url.replace(".", r"\.").replace("https://", ""), web(body))


def wrapped(payload: dict) -> str:
    """Models rarely emit bare JSON; wrap it in prose and a fence so
    _parse_json_object has to do real work."""
    return f"Here is my verdict.\n```json\n{json.dumps(payload)}\n```\nDone."


def claims(*statements, **fields) -> str:
    body = {"claims": list(statements)} if statements else {}
    body.update(fields)
    return json.dumps(body)


def verdict(
    winner_id,
    confidence="0.85",
    composite="0.80",
    support=None,
    scores=None,
    ranking=None,
    reasoning="Evidence A and B both confirm it.",
    **extra,
):
    """A well-formed adjudicator verdict: by default the winner's claim c0 is
    supported by both default sources and nothing else is scored."""
    support = list(support) if support is not None else list(SOURCES)
    claim_scores = scores if scores is not None else [
        {
            "interpretation_id": winner_id,
            "claim_id": "c0",
            "verdict": "supported",
            "support_source_urls": support,
            "note": "stated directly",
        }
    ]
    payload = {
        "winner_id": winner_id,
        "confidence": confidence,
        "composite_score": composite,
        "claim_scores": claim_scores,
        "reasoning": reasoning,
    }
    if ranking is not None:
        payload["ranking"] = ranking
    payload.update(extra)
    return wrapped(payload)


def submit(m, vm, sender, content, claims_json="", value=10):
    vm.sender = sender
    vm.value = value
    try:
        return m.submit_interpretation(content, claims_json)
    finally:
        vm.value = 0


def back(m, vm, sender, interpretation_id, value):
    vm.sender = sender
    vm.value = value
    try:
        return m.back_interpretation(interpretation_id)
    finally:
        vm.value = 0


def adjudicate_with(m, vm, caller, llm_response, body_a=None, body_b=None, fresh=True):
    """Mock both sources + the adjudicator, then adjudicate."""
    if fresh:
        vm.clear_mocks()
    if body_a is not None or body_b is not None:
        mock_sources(vm, body_a or "Evidence A.", body_b)
    else:
        mock_sources(vm)
    vm.mock_llm(ADJUDICATOR_RE, llm_response)
    vm.sender = caller
    vm.value = 0
    return m.adjudicate()


def decide(m, vm, caller, winner_id, confidence="0.85", **kw):
    return adjudicate_with(m, vm, caller, verdict(winner_id, confidence=confidence, **kw))


def finalize_after_window(m, vm, round_str="1"):
    advance(vm, 3601)
    vm.sender = vm._sender
    m.finalize(round_str)


@pytest.fixture
def monocle_source() -> str:
    return MONOCLE_PATH.read_text(encoding="utf-8")
