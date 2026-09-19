"""
MonocleVault: in-process custody ledger. Conservation invariants are
checked both through the live contract (end-to-end scenarios, with every
emitted transfer captured) and against the ledger class directly.
"""

import sys

import pytest
from gltest.direct import VMContext, create_test_addresses

from conftest import (
    advance,
    back,
    claims,
    decide,
    deploy_monocle,
    finalize_after_window,
    install_recorder,
    key,
    submit,
    to_hex,
)


def _assert_conserved(m):
    state = m.get_vault_state()
    assert state["conserved"] is True, state
    assert int(state["bucket_sum"]) == int(state["tracked"])
    return state


def test_every_operation_keeps_bucket_sum_equal_to_tracked():
    vm = VMContext()
    creator, alice, bob, carol, adder = create_test_addresses(5)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        inflow = 0

        vm.sender = adder
        vm.value = 5
        m.add_source("https://example.net/third", "corroborating")
        vm.value = 0
        inflow += 5
        assert _assert_conserved(m)["tracked"] == str(inflow)

        a = submit(m, vm, alice, "Rising.", claims("It rose"), 100)
        inflow += 100
        back(m, vm, carol, a, 60)
        inflow += 60
        b = submit(m, vm, bob, "Falling.", claims("It fell"), 80)
        inflow += 80
        _assert_conserved(m)

        decide(m, vm, creator, a)
        vm.sender = bob
        vm.value = 30
        m.challenge("1", b)
        vm.value = 0
        inflow += 30
        assert _assert_conserved(m)["tracked"] == str(inflow)

        advance(vm, 86401)
        m.finalize("1")  # challenge expired: bond refundable
        m.settle("1")
        for who in (alice, carol, bob):
            vm.sender = who
            m.claim("1")
        state = _assert_conserved(m)
        assert int(state["tracked"]) == inflow - rec.total_out()
        # Only the (still locked) source bond remains.
        assert state["tracked"] == "5"
        assert state["buckets"]["round:1"] == "0"


def test_total_paid_equals_total_deposited_after_everyone_claims():
    vm = VMContext()
    creator, a1, a2, b1 = create_test_addresses(4)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        # Round 1: inconclusive refunds.
        submit(m, vm, a1, "One.", claims("One"), 13)
        submit(m, vm, b1, "Two.", claims("Two"), 17)
        vm.clear_mocks()
        vm.sender = creator
        m.adjudicate()
        # Round 2: decided + finalized.
        w = submit(m, vm, a1, "Win.", claims("Win"), 29)
        back(m, vm, a2, w, 31)
        submit(m, vm, b1, "Lose.", claims("Lose"), 41)
        decide(m, vm, creator, w)
        finalize_after_window(m, vm, "2")
        m.settle("2")
        for who in (a1, b1):
            vm.sender = who
            m.claim("1")
        for who in (a1, a2, b1):
            vm.sender = who
            m.claim("2")
        assert rec.total_out() == 13 + 17 + 29 + 31 + 41
        assert _assert_conserved(m)["tracked"] == "0"


def test_residual_after_close_flushes_to_factory_receive_residual():
    """Forfeited source bonds never strand: after close they become residual
    and are sent to the factory's receive_residual() (factory-deployed)."""
    vm = VMContext()
    factory_like, creator, alice, adder = create_test_addresses(4)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, factory_like, creator_arg=to_hex(creator))
        vm.sender = adder
        vm.value = 9
        m.add_source("https://example.net/dead", "corroborating")
        vm.value = 0
        for i in range(3):
            iid = submit(m, vm, alice, f"Read {i}.", claims(f"C{i}"), 10)
            decide(m, vm, creator, iid, confidence="0.3")
        assert m.get_vault_state()["buckets"]["carry"] == "9"
        vm.sender = creator
        m.close_monocle()
        advance(vm, 7201)
        m.finalize_close()
        buckets = m.get_vault_state()["buckets"]
        assert buckets["carry"] == "0" and buckets["residual"] == "9"
        vm.sender = alice
        assert m.flush_residual() == "9"
        msg = rec.messages[-1]
        assert msg["to"] == key(factory_like)
        assert msg["method"] == "receive_residual"
        assert msg["value"] == 9
        with vm.expect_revert("No residual"):
            m.flush_residual()
        _assert_conserved(m)


def test_residual_of_directly_deployed_monocle_goes_to_deployer():
    vm = VMContext()
    deployer, alice, adder = create_test_addresses(3)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, deployer)
        vm.sender = adder
        vm.value = 6
        m.add_source("https://example.net/dead", "corroborating")
        vm.value = 0
        for i in range(3):
            iid = submit(m, vm, alice, f"Read {i}.", claims(f"C{i}"), 10)
            decide(m, vm, deployer, iid, confidence="0.3")
        vm.sender = deployer
        m.close_monocle()
        advance(vm, 7201)
        m.finalize_close()
        m.flush_residual()
        assert rec.transfers_to(deployer) == 6


def test_flush_residual_requires_closed():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        with vm.expect_revert("after the Monocle is closed"):
            m.flush_residual()


def test_close_makes_locked_source_bonds_refundable():
    vm = VMContext()
    creator, adder = create_test_addresses(2)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        vm.sender = adder
        vm.value = 5
        m.add_source("https://example.net/never-checked", "corroborating")
        vm.value = 0
        vm.sender = creator
        m.close_monocle()
        advance(vm, 7201)
        m.finalize_close()
        vm.sender = adder
        m.claim_source_bond("https://example.net/never-checked")
        assert rec.transfers_to(adder) == 5
        assert _assert_conserved(m)["tracked"] == "0"


# ----------------------------------------------------------------------
# Ledger class, directly
# ----------------------------------------------------------------------


class _FakeContract:
    def __init__(self):
        self.vault_buckets = {}
        self.vault_bucket_keys = []
        self.vault_tracked = 0
        self.balance = 0


@pytest.fixture
def ledger():
    vm = VMContext()
    creator, recipient = create_test_addresses(2)
    with vm.activate():
        rec = install_recorder(vm)
        deploy_monocle(vm, creator)
        mod = sys.modules["_contract_Monocle"]
        fake = _FakeContract()
        addr = recipient if hasattr(recipient, "as_hex") else mod.Address(recipient)
        yield mod, mod.MonocleVault(fake), fake, rec, addr


def test_ledger_credit_move_pay_conserve(ledger):
    mod, v, fake, rec, recipient = ledger
    v.credit("round:1", 100)
    v.credit("sources", 5)
    v.move("round:1", "carry", 30)
    assert v.balance_of("round:1") == 70 and v.balance_of("carry") == 30
    assert v.tracked() == 105
    v.pay("carry", 30, recipient)
    assert v.tracked() == 75
    fake.balance = 75
    state = v.state()
    assert state["conserved"] is True and state["solvent"] is True
    assert rec.total_out() == 30


def test_ledger_never_pays_from_an_empty_or_short_bucket(ledger):
    mod, v, fake, rec, recipient = ledger
    v.credit("round:1", 10)
    with pytest.raises(Exception, match="exceeds bucket balance"):
        v.pay("round:2", 1, recipient)
    with pytest.raises(Exception, match="exceeds bucket balance"):
        v.pay("round:1", 11, recipient)
    with pytest.raises(Exception, match="exceeds bucket balance"):
        v.move("round:1", "carry", 11)
    assert rec.total_out() == 0
    assert v.tracked() == 10


def test_ledger_rejects_negative_amounts(ledger):
    mod, v, fake, rec, recipient = ledger
    for fn in (lambda: v.credit("a", -1), lambda: v.move("a", "b", -1), lambda: v.pay("a", -1, recipient)):
        with pytest.raises(Exception, match="negative"):
            fn()


def test_ledger_zero_amounts_are_noops_without_transfers(ledger):
    mod, v, fake, rec, recipient = ledger
    v.credit("a", 0)
    v.pay("a", 0, recipient)
    assert rec.messages == []
    assert fake.vault_bucket_keys == []


def test_ledger_reports_insolvency(ledger):
    mod, v, fake, rec, recipient = ledger
    v.credit("round:1", 50)
    fake.balance = 49
    assert v.state()["solvent"] is False
