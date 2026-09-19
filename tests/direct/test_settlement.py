"""settle / claim / refunds / cancel_round / timelocked close, dust and
conservation guarantees, and close-griefing resistance."""

from gltest.direct import VMContext, create_test_addresses

from conftest import (
    advance,
    back,
    claims,
    decide,
    deploy_monocle,
    finalize_after_window,
    install_recorder,
    submit,
    to_hex,
)


def _finalized_round(m, vm, creator, winner_backers, loser_backers):
    """winner_backers/loser_backers: list of (addr, amount); first entry submits."""
    (w0, wamt), *wrest = winner_backers
    win = submit(m, vm, w0, "Correct read.", claims("Correct"), wamt)
    for addr, amt in wrest:
        back(m, vm, addr, win, amt)
    if loser_backers:
        (l0, lamt), *lrest = loser_backers
        lose = submit(m, vm, l0, "Wrong read.", claims("Wrong"), lamt)
        for addr, amt in lrest:
            back(m, vm, addr, lose, amt)
    decide(m, vm, creator, win)
    finalize_after_window(m, vm)
    return win


def test_settle_requires_finalized_round_not_merely_pending():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Only.", claims("X"), 10)
        with vm.expect_revert("only a FINAL decision"):
            m.settle("1")
        decide(m, vm, creator, a)
        with vm.expect_revert("only a FINAL decision"):
            m.settle("1")


def test_settle_marks_round_settled_once():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _finalized_round(m, vm, creator, [(alice, 10)], [])
        m.settle("1")
        assert m.get_round_info("1")["status"] == "settled"
        assert m.get_finality("1")["finality"] == "final"
        with vm.expect_revert("only a FINAL decision"):
            m.settle("1")


def test_claim_requires_claimable_round():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _finalized_round(m, vm, creator, [(alice, 10)], [])
        vm.sender = alice
        with vm.expect_revert("not yet claimable"):
            m.claim("1")


def test_claim_pays_parimutuel_share_of_winning_side():
    """Alice 100 + Carol 100 on the winner; Bob 200 on the loser. Pool 400;
    each winner gets 200."""
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        _finalized_round(m, vm, creator, [(alice, 100), (carol, 100)], [(bob, 200)])
        m.settle("1")
        assert m.get_claimable("1", to_hex(alice)) == "200"
        assert m.get_claimable("1", to_hex(carol)) == "200"
        assert m.get_claimable("1", to_hex(bob)) == "0"
        vm.sender = alice
        assert m.claim("1") == "200"
        assert m.is_claimed("1", to_hex(alice)) is True
        assert m.get_claimable("1", to_hex(alice)) == "0"
        assert rec.transfers_to(alice) == 200
        assert rec.messages[-1]["on"] == "finalized"


def test_claim_on_losing_side_succeeds_with_zero_payout():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        _finalized_round(m, vm, creator, [(alice, 100)], [(bob, 50)])
        m.settle("1")
        vm.sender = bob
        assert m.claim("1") == "0"
        assert m.is_claimed("1", to_hex(bob)) is True
        assert rec.transfers_to(bob) == 0


def test_claim_without_participation_reverts():
    vm = VMContext()
    creator, alice, outsider = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _finalized_round(m, vm, creator, [(alice, 100)], [])
        m.settle("1")
        vm.sender = outsider
        with vm.expect_revert("No stake"):
            m.claim("1")


def test_double_claim_reverts():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _finalized_round(m, vm, creator, [(alice, 100)], [])
        m.settle("1")
        vm.sender = alice
        m.claim("1")
        with vm.expect_revert("Already claimed"):
            m.claim("1")


def test_rounding_dust_goes_to_last_winning_claimant_never_strands():
    """Pot 100 split over winner stakes 10/10/10: 33 + 33 + 34 = 100 exactly."""
    vm = VMContext()
    creator, a1, a2, a3, loser = create_test_addresses(5)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        _finalized_round(m, vm, creator, [(a1, 10), (a2, 10), (a3, 10)], [(loser, 70)])
        m.settle("1")
        payouts = []
        for who in (a1, a2, a3):
            vm.sender = who
            payouts.append(int(m.claim("1")))
        assert payouts == [33, 33, 34]
        assert sum(payouts) == 100
        assert m.get_round_info("1")["paid_winners"] == "100"
        assert m.get_vault_state()["buckets"]["round:1"] == "0"
        assert rec.total_out() == 100


def test_claimed_amounts_never_exceed_round_pool():
    vm = VMContext()
    creator, a1, a2, b1, b2 = create_test_addresses(5)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        _finalized_round(m, vm, creator, [(a1, 37), (a2, 11)], [(b1, 23), (b2, 41)])
        m.settle("1")
        for who in (b2, a2, b1, a1):
            vm.sender = who
            m.claim("1")
        assert rec.total_out() == 37 + 11 + 23 + 41
        assert m.get_vault_state()["tracked"] == "0"


def test_inconclusive_round_is_claimable_as_refund():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        submit(m, vm, alice, "Only.", claims("X"), 100)
        vm.clear_mocks()
        vm.sender = creator
        m.adjudicate()
        assert m.get_round_info("1")["status"] == "inconclusive"
        assert m.get_finality("1")["finality"] == "refund"
        vm.sender = alice
        assert m.claim("1") == "100"
        assert rec.transfers_to(alice) == 100


def test_refund_sums_stake_across_multiple_backed_interpretations():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        submit(m, vm, alice, "First.", claims("One"), 60)
        submit(m, vm, alice, "Second.", claims("Two"), 40)
        vm.clear_mocks()
        vm.sender = creator
        m.adjudicate()
        assert m.get_claimable("1", to_hex(alice)) == "100"


def test_cancel_round_before_timeout_reverts():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        submit(m, vm, alice, "Content.", claims("X"), 10)
        with vm.expect_revert("can only be cancelled after"):
            m.cancel_round("1")


def test_cancel_round_after_timeout_unlocks_refund_and_opens_next_round():
    """current_round must never be left on the cancelled round, or nothing
    could ever be submitted again."""
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        submit(m, vm, alice, "Nobody adjudicates this.", claims("X"), 100)
        advance(vm, 86401)
        vm.sender = alice  # permissionless
        m.cancel_round("1")
        assert m.get_round_info("1")["status"] == "cancelled"
        assert m.get_claimable("1", to_hex(alice)) == "100"
        assert m.get_current_round() == "2"
        assert submit(m, vm, alice, "Life goes on.", claims("Y"), 10).startswith("2-")
        vm.sender = alice
        m.claim("1")
        assert m.is_claimed("1", to_hex(alice)) is True


def test_cancel_round_rejects_decided_rounds():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Only.", claims("X"), 10)
        decide(m, vm, creator, a)
        advance(vm, 86401)
        with vm.expect_revert("cannot be cancelled"):
            m.cancel_round("1")


def test_claim_cannot_drain_a_different_rounds_pool():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _finalized_round(m, vm, creator, [(alice, 100)], [(bob, 50)])
        m.settle("1")
        submit(m, vm, alice, "Round 2 pick.", claims("Round two"), 30)
        vm.clear_mocks()
        vm.sender = creator
        m.adjudicate()
        assert m.get_round_info("2")["status"] == "inconclusive"
        assert m.get_claimable("1", to_hex(alice)) == "150"
        assert m.get_claimable("2", to_hex(alice)) == "30"
        vm.sender = alice
        m.claim("1")
        assert m.is_claimed("2", to_hex(alice)) is False
        assert m.get_claimable("2", to_hex(alice)) == "30"
        m.claim("2")
        assert m.get_claimable("1", to_hex(alice)) == "0"
        assert m.get_claimable("2", to_hex(alice)) == "0"


# ----------------------------------------------------------------------
# Timelocked close (no instant escape hatch)
# ----------------------------------------------------------------------


def test_close_only_creator():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        vm.sender = alice
        with vm.expect_revert("Only the Monocle creator"):
            m.close_monocle()
        vm.sender = creator
        m.close_monocle()
        info = m.get_monocle_info()
        assert info["status"] == "closing"
        assert int(info["close_executable_at"]) == int(info["close_requested_at"]) + 7200


def test_close_starts_timelock_and_cannot_instantly_cancel_open_round():
    """A losing creator must not be able to close and instantly refund the
    round: it stays open through the timelock."""
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        submit(m, vm, alice, "Alice.", claims("A"), 100)
        submit(m, vm, bob, "Creator-favoured.", claims("B"), 50)
        vm.sender = creator
        m.close_monocle()
        assert m.get_round_info("1")["status"] == "open"
        with vm.expect_revert("timelock has not elapsed"):
            m.finalize_close()
        advance(vm, 7199)
        with vm.expect_revert("timelock has not elapsed"):
            m.finalize_close()
        assert m.get_round_info("1")["status"] == "open"


def test_backers_can_adjudicate_and_settle_during_close_timelock():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Alice.", claims("A"), 100)
        submit(m, vm, creator, "Creator's losing read.", claims("B"), 50)
        vm.sender = creator
        m.close_monocle()
        decide(m, vm, alice, a)  # alice forces adjudication inside the window
        finalize_after_window(m, vm)
        m.settle("1")
        vm.sender = alice
        m.claim("1")
        assert rec.transfers_to(alice) == 150
        # Round 2 opened while closing; after the timelock it is cancelled.
        advance(vm, 7201)
        m.finalize_close()
        assert m.get_monocle_info()["status"] == "closed"
        assert m.get_round_info("2")["status"] == "cancelled"


def test_close_after_timelock_cancels_open_round_and_refunds():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        submit(m, vm, alice, "Alice.", claims("A"), 100)
        submit(m, vm, bob, "Bob.", claims("B"), 50)
        vm.sender = creator
        m.close_monocle()
        advance(vm, 7201)
        vm.sender = bob  # finalize_close is permissionless
        m.finalize_close()
        assert m.get_monocle_info()["status"] == "closed"
        assert m.get_round_info("1")["status"] == "cancelled"
        assert m.get_claimable("1", to_hex(alice)) == "100"
        assert m.get_claimable("1", to_hex(bob)) == "50"
        vm.sender = alice
        m.claim("1")


def test_close_does_not_touch_already_finalized_rounds():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _finalized_round(m, vm, creator, [(alice, 100)], [])
        vm.sender = creator
        m.close_monocle()
        advance(vm, 7201)
        m.finalize_close()
        assert m.get_round_info("1")["status"] == "finalized"
        assert m.get_round_info("2")["status"] == "cancelled"
        m.settle("1")
        assert m.get_claimable("1", to_hex(alice)) == "100"


def test_finalize_close_waits_for_pending_round():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Alice.", claims("A"), 100)
        decide(m, vm, alice, a)
        vm.sender = creator
        m.close_monocle()
        advance(vm, 7201)
        with vm.expect_revert("must finish its challenge window"):
            m.finalize_close()
        m.finalize("1")
        m.finalize_close()
        assert m.get_monocle_info()["status"] == "closed"


def test_cancel_close_restores_active():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        vm.sender = alice
        with vm.expect_revert("Only the Monocle creator"):
            m.cancel_close()
        vm.sender = creator
        with vm.expect_revert("not closing"):
            m.cancel_close()
        m.close_monocle()
        m.cancel_close()
        assert m.get_monocle_info()["status"] == "active"
        submit(m, vm, alice, "Back to business.", claims("X"), 10)


def test_close_twice_and_finalize_close_when_not_closing_revert():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        vm.sender = creator
        with vm.expect_revert("not closing"):
            m.finalize_close()
        m.close_monocle()
        with vm.expect_revert("already closing"):
            m.close_monocle()
        advance(vm, 7201)
        m.finalize_close()
        with vm.expect_revert("already closed"):
            m.close_monocle()
