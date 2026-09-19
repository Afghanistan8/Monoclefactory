"""
System tests: real multi-contract execution in-process (see glsim_harness).
MonocleFactory really deploys MonocleReputation and each Monocle; the
reputation aggregator really reads the factory registry and Monocle rounds
through cross-contract views; flush_residual's message really lands in
MonocleFactory.receive_residual with its value.
"""

import pytest

from conftest import (
    ADJUDICATOR_RE,
    FACTORY_PATH,
    MONOCLE_PATH,
    REPUTATION_PATH,
    SOURCES,
    claims,
    mock_sources,
    verdict,
    web,
)
from glsim_harness import System

OWNER = "0x" + "11" * 20
ALICE = "0x" + "22" * 20
BOB = "0x" + "33" * 20
CAROL = "0x" + "44" * 20
ADDER = "0x" + "55" * 20


@pytest.fixture
def sysm():
    s = System()
    try:
        yield s
    finally:
        s.close()


def _factory(s, creation_stake=100):
    return s.deploy(
        FACTORY_PATH,
        [MONOCLE_PATH.read_text(encoding="utf-8"), REPUTATION_PATH.read_text(encoding="utf-8"), creation_stake, 10, 5, 20, 180],
        OWNER,
    )


def _create(s, factory, sender=ALICE, value=100):
    return s.call(factory, "create_monocle", list(SOURCES), "market", "System test", "Desc", "", sender=sender, value=value)


def test_factory_deploys_real_children_with_real_creator(sysm):
    s = sysm
    factory = _factory(s)
    rep = s.view(factory, "get_reputation_address")
    # Under gltest the factory constructor runs at a phantom address (shim 2
    # in glsim_harness); it must resolve to the very same factory instance.
    recorded = s.view(rep, "get_factory").lower()
    assert s.engine._instances[recorded] is s.engine._instances[factory.lower()]
    assert s.view(recorded, "get_owner").lower() == OWNER

    monocle = _create(s, factory, value=130)
    info = s.view(monocle, "get_monocle_info")
    assert info["creator"].lower() == ALICE
    assert info["factory_address"].lower() == factory.lower()
    assert info["deployed_by_factory"] is True
    assert info["min_challenge_bond"] == "20"
    assert info["challenge_window_seconds"] == "180"  # the factory's deployment-wide window
    assert s.view(factory, "get_collected_fees") == "100"
    assert s.paid_to(ALICE) == 30  # excess refund really emitted
    # The real creator (not the factory) can close it.
    s.call(monocle, "close_monocle", sender=ALICE)
    assert s.view(monocle, "get_monocle_info")["status"] == "closing"


def test_full_lifecycle_and_reputation_sync_via_cross_contract_views(sysm):
    s = sysm
    factory = _factory(s)
    rep = s.view(factory, "get_reputation_address")
    monocle = _create(s, factory)

    s.call(monocle, "add_source", "https://example.net/third", "corroborating", sender=ADDER, value=5)
    a = s.call(monocle, "submit_interpretation", "Rising.", claims("It rose"), sender=ALICE, value=100)
    s.call(monocle, "back_interpretation", a, sender=CAROL, value=50)
    s.call(monocle, "submit_interpretation", "Falling.", claims("It fell"), sender=BOB, value=150)

    s.vm.clear_mocks()
    mock_sources(s.vm)
    s.vm.mock_web(r"example\.net/third", web("Third: it rose."))
    s.vm.mock_llm(ADJUDICATOR_RE, verdict(a))
    assert s.call(monocle, "adjudicate", sender=BOB) == a

    with pytest.raises(Exception, match="not final"):
        s.call(rep, "record_round", monocle, "1", sender=CAROL)

    s.advance(3601)
    s.call(monocle, "finalize", "1", sender=CAROL)
    s.call(monocle, "settle", "1", sender=CAROL)
    for who in (ALICE, CAROL, BOB):
        s.call(monocle, "claim", "1", sender=who)
    s.call(monocle, "claim_source_bond", "https://example.net/third", sender=ADDER)
    assert s.paid_to(ALICE) == 200  # 100/150 of the 300 pot
    assert s.paid_to(CAROL) == 100
    assert s.paid_to(BOB) == 0
    assert s.paid_to(ADDER) == 5
    assert s.view(monocle, "get_vault_state")["tracked"] == "0"

    applied = s.call(rep, "record_round", monocle, "1", sender=CAROL)
    assert applied["wins"] == [ALICE] and applied["losses"] == [BOB]
    assert s.view(rep, "get_reputation", ALICE)["decided_wins"] == 1
    assert s.view(rep, "get_reputation", BOB)["decided_losses"] == 1
    with pytest.raises(Exception, match="already recorded"):
        s.call(rep, "record_round", monocle, "1", sender=CAROL)


def test_reputation_rejects_monocle_not_registered_with_factory(sysm):
    s = sysm
    factory = _factory(s)
    rep = s.view(factory, "get_reputation_address")
    rogue = s.deploy(MONOCLE_PATH, [list(SOURCES), "market", "Rogue", "", "", 10, 5, 20, 3600, ""], BOB)
    with pytest.raises(Exception, match="not registered"):
        s.call(rep, "record_round", rogue, "1", sender=BOB)


def test_residual_flush_lands_in_factory_and_is_withdrawable(sysm):
    s = sysm
    factory = _factory(s, creation_stake=0)
    monocle = _create(s, factory, value=0)
    s.call(monocle, "add_source", "https://example.net/dead", "corroborating", sender=ADDER, value=8)
    for i in range(3):
        iid = s.call(monocle, "submit_interpretation", f"Read {i}.", claims(f"C{i}"), sender=ALICE, value=10)
        s.vm.clear_mocks()
        mock_sources(s.vm)
        s.vm.mock_llm(ADJUDICATOR_RE, verdict(iid, confidence="0.3"))
        s.call(monocle, "adjudicate", sender=ALICE)
    s.call(monocle, "close_monocle", sender=ALICE)
    s.advance(7201)
    s.call(monocle, "finalize_close", sender=BOB)
    assert s.call(monocle, "flush_residual", sender=BOB) == "8"
    assert s.view(factory, "get_residual_fees") == "8"
    assert s.call(factory, "withdraw_fees", sender=OWNER) == "8"
    assert s.paid_to(OWNER) == 8
