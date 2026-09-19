"""
Integration tests against a real GenLayer node: Studio Next (studio_devnet,
chain 61997) or a local Studio (localnet, 61127).

    gltest tests/integration -v --network studio_devnet
    gltest tests/integration -v                       # localnet default

The whole module skips cleanly when the configured RPC is unreachable, so
`gltest tests/integration` is safe to run without a node.

Every write that produces state other flows act on waits for FINALIZED.
adjudicate/resolve raise consensus_max_rotations to 6 (see docs/STUDIO_NEXT.md).

The challenge window is one hour on-chain. The part of the lifecycle after
it (finalize -> settle -> claim) only runs when MONOCLE_INTEGRATION_LONG=1,
because it genuinely waits out CHALLENGE_WINDOW_SECONDS.
"""

import json
import os
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
MONOCLE_PATH = CONTRACTS / "Monocle.py"
FACTORY_PATH = CONTRACTS / "MonocleFactory.py"
REPUTATION_PATH = CONTRACTS / "MonocleReputation.py"

# Stable, verifiable pages.
SOURCES = ["https://en.wikipedia.org/wiki/Speed_of_light", "https://simple.wikipedia.org/wiki/Speed_of_light"]
HEAVY_ROTATIONS = 6

pytestmark = pytest.mark.integration


def _rpc_reachable() -> bool:
    try:
        from gltest_cli.config.general import get_general_config

        url = get_general_config().get_rpc_url()
    except Exception:
        return False
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def _require_node():
    if not _rpc_reachable():
        pytest.skip("No reachable GenLayer RPC for the configured network; skipping integration tests.")


def _finalized(call, value=0, rotations=None):
    kwargs = {"value": value, "wait_until": "finalized"}
    if rotations:
        kwargs["consensus_max_rotations"] = rotations
    receipt = call.transact(**kwargs)
    from gltest.assertions import tx_execution_succeeded

    assert tx_execution_succeeded(receipt), receipt
    return receipt


@pytest.fixture(scope="module")
def factory(accounts):
    from gltest import get_contract_factory

    cf = get_contract_factory(contract_file_path=str(FACTORY_PATH))
    return cf.deploy(
        args=[
            MONOCLE_PATH.read_text(encoding="utf-8"),
            REPUTATION_PATH.read_text(encoding="utf-8"),
            1,  # creation stake (wei) so withdraw_fees has something real
            1,  # min interpretation bond
            1,  # min source bond
            1,  # min challenge bond
        ],
        wait_until="finalized",
    )


def _new_monocle(factory, account, title):
    from gltest import get_contract_factory

    before = factory.get_monocles_count().call()
    _finalized(factory.connect(account).create_monocle(args=[SOURCES, "research", title, "integration", ""]), value=1)
    address = factory.get_monocles_page(args=[before, 1]).call()["addresses"][0]
    return get_contract_factory(contract_file_path=str(MONOCLE_PATH)).build_contract(contract_address=address)


def test_create_spawns_readable_child_with_real_creator(factory, accounts):
    creator = accounts[0]
    monocle = _new_monocle(factory, creator, "Integration: creator")
    info = monocle.get_monocle_info().call()
    assert info["creator"].lower() == creator.address.lower()
    assert info["deployed_by_factory"] is True
    meta = factory.get_monocle_meta(args=[monocle.address]).call()
    assert meta["title"] == "Integration: creator"


def test_factory_rejects_single_source(factory, accounts):
    with pytest.raises(Exception, match="At least 2 sources"):
        _finalized(factory.connect(accounts[0]).create_monocle(args=[["https://example.com"], "r", "t", "d", ""]), value=1)


def test_withdraw_fees_recovers_exact_creation_stakes(factory, accounts):
    owner = accounts[0]
    before = int(factory.get_collected_fees().call())
    _new_monocle(factory, owner, "Integration: fees")
    assert int(factory.get_collected_fees().call()) == before + 1
    with pytest.raises(Exception, match="Only the factory owner"):
        _finalized(factory.connect(accounts[1]).withdraw_fees(args=[]))
    _finalized(factory.connect(owner).withdraw_fees(args=[]))
    assert factory.get_collected_fees().call() == "0"


def test_lifecycle_to_pending_and_optionally_to_claim(factory, accounts):
    creator, alice, bob = accounts[0], accounts[1], accounts[2]
    monocle = _new_monocle(factory, creator, "What is the speed of light in vacuum?")

    _finalized(monocle.connect(bob).add_source(args=["https://en.wikipedia.org/wiki/Metre", "corroborating"]), value=1)
    _finalized(
        monocle.connect(alice).submit_interpretation(
            args=["Light in vacuum travels at exactly 299,792,458 m/s.", json.dumps({"claims": ["The speed of light in vacuum is exactly 299,792,458 metres per second"], "value_m_per_s": "299792458"})]
        ),
        value=2,
    )
    _finalized(
        monocle.connect(bob).submit_interpretation(
            args=["Light in vacuum travels at about 150,000 km/s.", json.dumps({"claims": ["The speed of light in vacuum is about 150,000 kilometres per second"], "value_m_per_s": "150000000"})]
        ),
        value=2,
    )
    id_correct = monocle.get_round_interpretations(args=["1"]).call()[0]["id"]

    _finalized(monocle.connect(creator).adjudicate(args=[]), rotations=HEAVY_ROTATIONS)
    round1 = monocle.get_round_info(args=["1"]).call()
    # A live LLM may legitimately fail closed; either way nothing is final yet.
    assert round1["status"] in ("decided_pending", "inconclusive")
    snapshot = monocle.get_evidence_snapshot(args=["1"]).call()
    assert isinstance(snapshot["evidence_snapshot"], list)

    if round1["status"] == "inconclusive":
        _finalized(monocle.connect(alice).claim(args=["1"]))
        return

    assert round1["pending_winner"] == id_correct
    live = monocle.get_live_interpretation().call()
    assert live["finality"] == "pending" and live["has_live"] is False

    if os.environ.get("MONOCLE_INTEGRATION_LONG") != "1":
        pytest.skip("Set MONOCLE_INTEGRATION_LONG=1 to wait out the 1h challenge window.")

    time.sleep(3605)
    _finalized(monocle.connect(bob).finalize(args=["1"]))
    live = monocle.get_live_interpretation().call()
    assert live["finality"] == "final" and live["interpretation"]["id"] == id_correct
    _finalized(monocle.connect(bob).settle(args=["1"]))
    assert int(monocle.get_claimable(args=["1", alice.address]).call()) == 4
    _finalized(monocle.connect(alice).claim(args=["1"]))
    assert monocle.is_claimed(args=["1", alice.address]).call() is True

    from gltest import get_contract_factory

    rep = get_contract_factory(contract_file_path=str(REPUTATION_PATH)).build_contract(
        contract_address=factory.get_reputation_address().call()
    )
    _finalized(rep.connect(bob).record_round(args=[monocle.address, "1"]))
    assert rep.get_reputation(args=[alice.address]).call()["decided_wins"] == 1
