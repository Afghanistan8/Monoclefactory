"""
In-process multi-contract harness built on glsim's SimEngine (bundled with
genlayer-test 0.30.0rc2). It really executes child deploys, cross-contract
views and internal messages, which plain direct mode cannot.

Three shims, each for a verified glsim/RC mismatch (not contract behaviour):
  1. The RC SDK encodes the method name under calldata key "" while glsim
     reads "method"; without the shim every cross-contract view fails and
     every message is dropped.
  2. gltest's deploy_contract runs a top-level constructor at sha256(path)
     instead of the final address, so children deployed in a constructor
     (MonocleFactory -> MonocleReputation) see a phantom parent. We alias
     that address to the real instance.
  3. glsim executes queued messages without their value. We run the queue
     ourselves, with value, and record plain transfers to EOAs in a ledger.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("GENVM_VERSION", "v0.6.0-rc5")

import hashlib  # noqa: E402

from glsim.engine import SimEngine  # noqa: E402
from glsim.state import StateStore  # noqa: E402

from conftest import warp_to, now_ts  # noqa: E402


def _addr_key(address) -> str:
    if hasattr(address, "as_bytes"):
        return "0x" + address.as_bytes.hex()
    if isinstance(address, bytes):
        return "0x" + address.hex()
    return str(address).lower()


class System:
    def __init__(self, chain_id: int = 61127):
        self.state = StateStore(chain_id=chain_id, seed="monocle-tests")
        self.engine = SimEngine(self.state)
        self.engine.activate()
        self.vm = self.engine.vm
        self.transfers = []  # (to, value) plain transfers to non-contracts
        self.queue = []
        inner = self.vm._gl_call_hook

        def hook(vm, request):
            for kind in ("CallContract", "EmitInternalMessage"):
                if kind in request:
                    cd = request[kind].get("calldata") or {}
                    if isinstance(cd, dict) and "" in cd and "method" not in cd:
                        cd = dict(cd)
                        cd["method"] = cd[""]
                        request[kind]["calldata"] = cd
            if "EmitInternalMessage" in request:
                msg = request["EmitInternalMessage"]
                target = _addr_key(msg.get("address"))
                method = (msg.get("calldata") or {}).get("method")
                value = int(msg.get("value", 0))
                sender = "0x" + vm._contract_address.hex()
                if method and target in self.engine._instances:
                    self.queue.append((target, method, msg["calldata"].get("args", []), value, sender))
                else:
                    self.transfers.append((target, value))
                return {"ok": None}
            return inner(vm, request)

        self.vm._gl_call_hook = hook

    def close(self):
        self.engine.deactivate()

    # -- time --------------------------------------------------------------
    def advance(self, seconds: int):
        warp_to(self.vm, now_ts(self.vm) + seconds)

    # -- calls -------------------------------------------------------------
    def deploy(self, path: Path, args: list, sender: str) -> str:
        address, instance = self.engine.deploy(str(path), args, sender=sender)
        phantom = "0x" + hashlib.sha256(str(Path(path).resolve()).encode()).digest()[:20].hex()
        self.engine._instances[phantom] = instance
        self.engine._storages[phantom] = self.engine._storages[address.lower()]
        return address

    def call(self, address: str, method: str, *args, sender: str, value: int = 0):
        self.vm.value = value
        try:
            result = self.engine.call_method(address, method, list(args), sender=sender)
        finally:
            self.vm.value = 0
        self._drain()
        return result

    def view(self, address: str, method: str, *args):
        return self.engine.call_method(address, method, list(args))

    def _drain(self):
        while self.queue:
            target, method, args, value, sender = self.queue.pop(0)
            self.vm.value = value
            try:
                self.engine.call_method(target, method, args, sender=sender)
            finally:
                self.vm.value = 0

    def paid_to(self, address: str) -> int:
        return sum(v for to, v in self.transfers if to == address.lower())
