"""
Challenge window, bonded challenges, resolve_challenge and finalize.
A pending winner may only become the FINAL live output after the window
elapses unchallenged, or after a challenge resolves (or expires).
"""

import json

from gltest.direct import VMContext, create_test_addresses

from conftest import (
    ARBITER_RE,
    advance,
    back,
    capture_llm,
    claims,
    decide,
    deploy_monocle,
    install_recorder,
    key,
    mock_sources,
    run_tx,
    submit,
    to_hex,
    wrapped,
)


def _pending(m, vm, creator, alice, bob, alice_stake=100, bob_stake=100):
    a = submit(m, vm, alice, "Rising.", claims("It rose"), alice_stake)
    b = submit(m, vm, bob, "Falling.", claims("It fell"), bob_stake)
    decide(m, vm, creator, a)
    return a, b


def _challenge(m, vm, who, alt, bond=20, round_str="1"):
    vm.sender = who
    vm.value = bond
    try:
        m.challenge(round_str, alt)
    finally:
        vm.value = 0


def _resolve(m, vm, caller, preferred, confidence="0.9", round_str="1"):
    vm.clear_mocks()
    mock_sources(vm)
    vm.mock_llm(ARBITER_RE, wrapped({"preferred_id": preferred, "confidence": confidence, "reasoning": "Per A and B."}))
    vm.sender = caller
    return m.resolve_challenge(round_str)


# ----------------------------------------------------------------------
# Window and finalize
# ----------------------------------------------------------------------


def test_challenge_window_prevents_live_update():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a, _ = _pending(m, vm, creator, alice, bob)
        with vm.expect_revert("Challenge window is still open"):
            m.finalize("1")
        advance(vm, 3600)  # exactly at the deadline: still open
        with vm.expect_revert("Challenge window is still open"):
            m.finalize("1")
        live = m.get_live_interpretation()
        assert live["has_live"] is False
        assert live["finality"] == "pending"
        pending = m.get_pending_interpretation()
        assert pending["has_pending"] is True
        assert pending["interpretation"]["id"] == a


def test_finalize_after_window_updates_live_and_opens_next_round():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a, _ = _pending(m, vm, creator, alice, bob)
        advance(vm, 3601)
        vm.sender = bob  # permissionless
        m.finalize("1")
        live = m.get_live_interpretation()
        assert live["has_live"] is True
        assert live["finality"] == "final"
        assert live["interpretation"]["id"] == a
        assert live["reasoning"]["final_winner_id"] == a
        assert live["pending"] == {}
        r1 = m.get_round_info("1")
        assert r1["status"] == "finalized"
        assert r1["winner_id"] == a
        assert r1["pot"] == "200"
        assert m.get_finality("1")["finality"] == "final"
        assert m.get_current_round() == "2"
        assert m.get_pending_interpretation() == {"has_pending": False}
        with vm.expect_revert("not awaiting finalization"):
            m.finalize("1")


def test_finalize_rejects_rounds_that_never_decided():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        submit(m, vm, alice, "Read.", claims("X"), 10)
        with vm.expect_revert("not awaiting finalization"):
            m.finalize("1")


# ----------------------------------------------------------------------
# challenge() preconditions
# ----------------------------------------------------------------------


def test_challenge_requires_decided_pending_round():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        b = submit(m, vm, bob, "Falling.", claims("It fell"), 10)
        with vm.expect_revert("nothing to challenge"):
            _challenge(m, vm, alice, b)


def test_challenge_requires_bond():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _, b = _pending(m, vm, creator, alice, bob)
        with vm.expect_revert("Challenge bond too low"):
            _challenge(m, vm, bob, b, bond=19)


def test_challenge_alternative_must_be_a_different_interpretation_in_the_round():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a, _ = _pending(m, vm, creator, alice, bob)
        with vm.expect_revert("must differ"):
            _challenge(m, vm, bob, a)
        with vm.expect_revert("not part of this round"):
            _challenge(m, vm, bob, "9-9")


def test_challenge_after_window_is_rejected():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _, b = _pending(m, vm, creator, alice, bob)
        advance(vm, 3601)
        with vm.expect_revert("window for this round has closed"):
            _challenge(m, vm, bob, b)


def test_only_one_challenge_per_round():
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _, b = _pending(m, vm, creator, alice, bob)
        _challenge(m, vm, bob, b)
        assert m.get_round_info("1")["status"] == "challenged"
        with vm.expect_revert("nothing to challenge"):
            _challenge(m, vm, carol, b)


def test_challenged_round_cannot_be_finalized_before_resolution():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _, b = _pending(m, vm, creator, alice, bob)
        _challenge(m, vm, bob, b)
        advance(vm, 3601)
        with vm.expect_revert("call resolve_challenge first"):
            m.finalize("1")
        assert m.get_live_interpretation()["has_live"] is False


def test_resolve_requires_an_open_challenge():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _pending(m, vm, creator, alice, bob)
        with vm.expect_revert("no open challenge"):
            m.resolve_challenge("1")


# ----------------------------------------------------------------------
# Outcomes
# ----------------------------------------------------------------------


def test_successful_challenge_replaces_pending_winner_and_rewards_challenger():
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        a, b = _pending(m, vm, creator, alice, bob, alice_stake=100, bob_stake=100)
        _challenge(m, vm, carol, b, bond=30)
        assert _resolve(m, vm, alice, b, confidence="0.9") == "upheld"

        live = m.get_live_interpretation()
        assert live["finality"] == "final"
        assert live["interpretation"]["id"] == b
        r1 = m.get_round_info("1")
        assert r1["status"] == "finalized"
        assert r1["winner_id"] == b
        # reward = min(bond=30, pool 200 * 10%) = 20 ; challenger gets 30 + 20
        assert r1["challenge"]["outcome"] == "upheld"
        assert r1["challenge"]["reward"] == "20"
        assert r1["challenge"]["payout"] == "50"
        # pot = 200 stake + 30 bond - 50 challenger payout = 180
        assert r1["pot"] == "180"
        assert r1["reasoning"]["challenge_outcome"] == "upheld"

        m.settle("1")
        assert m.get_claimable("1", to_hex(carol)) == "50"
        assert m.get_claimable("1", to_hex(bob)) == "180"
        assert m.get_claimable("1", to_hex(alice)) == "0"
        for who in (carol, bob, alice):
            vm.sender = who
            m.claim("1")
        assert rec.transfers_to(carol) == 50
        assert rec.transfers_to(bob) == 180
        assert rec.total_out() == 230  # everything in, everything out
        assert m.get_vault_state()["tracked"] == "0"


def test_challenger_reward_is_capped_at_bond():
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _, b = _pending(m, vm, creator, alice, bob, alice_stake=1000, bob_stake=1000)
        _challenge(m, vm, carol, b, bond=20)
        _resolve(m, vm, alice, b)
        ch = m.get_round_info("1")["challenge"]
        assert ch["reward"] == "20"  # 10% of 2000 = 200, capped at bond 20
        assert ch["payout"] == "40"


def test_failed_challenge_forfeits_bond_to_winning_backers():
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        a, b = _pending(m, vm, creator, alice, bob)
        _challenge(m, vm, carol, b, bond=40)
        assert _resolve(m, vm, bob, a, confidence="0.8") == "rejected"
        r1 = m.get_round_info("1")
        assert r1["winner_id"] == a
        assert r1["challenge"]["payout"] == "0"
        assert r1["pot"] == "240"  # 200 stake + 40 forfeited bond
        m.settle("1")
        assert m.get_claimable("1", to_hex(alice)) == "240"
        vm.sender = carol
        assert m.claim("1") == "0"  # participated, nothing owed
        vm.sender = alice
        m.claim("1")
        assert rec.transfers_to(alice) == 240
        assert rec.transfers_to(carol) == 0


def test_low_confidence_resolution_is_unresolved_refunds_bond_original_stands():
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a, b = _pending(m, vm, creator, alice, bob)
        _challenge(m, vm, carol, b, bond=25)
        assert _resolve(m, vm, alice, b, confidence="0.4") == "unresolved"
        r1 = m.get_round_info("1")
        assert r1["winner_id"] == a
        assert r1["challenge"]["payout"] == "25"
        assert r1["pot"] == "200"
        m.settle("1")
        assert m.get_claimable("1", to_hex(carol)) == "25"


def test_invalid_arbiter_pick_is_unresolved():
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a, b = _pending(m, vm, creator, alice, bob)
        _challenge(m, vm, carol, b)
        assert _resolve(m, vm, alice, "ghost-id", confidence="0.99") == "unresolved"
        assert m.get_round_info("1")["winner_id"] == a


def test_no_evidence_resolution_is_unresolved_without_llm_call():
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a, b = _pending(m, vm, creator, alice, bob)
        _challenge(m, vm, carol, b)
        vm.clear_mocks()
        cap = capture_llm(vm, wrapped({"preferred_id": b, "confidence": "0.99"}))
        vm.sender = alice
        assert m.resolve_challenge("1") == "unresolved"
        assert cap.prompts == []
        assert m.get_round_info("1")["winner_id"] == a


def test_unresolvable_challenge_expires_and_original_finalizes():
    """A challenge whose resolution keeps failing cannot strand the round:
    after 24h finalize() lets the original stand and refunds the bond."""
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a, b = _pending(m, vm, creator, alice, bob)
        _challenge(m, vm, carol, b, bond=20)
        vm.clear_mocks()
        mock_sources(vm)
        vm.mock_llm(ARBITER_RE, "no json here")
        with vm.expect_revert("LLM_MALFORMED"):
            run_tx(vm, m.resolve_challenge, "1")
        assert m.get_round_info("1")["status"] == "challenged"
        advance(vm, 86401)
        m.finalize("1")
        r1 = m.get_round_info("1")
        assert r1["winner_id"] == a
        assert r1["challenge"]["outcome"] == "expired"
        assert r1["challenge"]["payout"] == "20"


def test_arbiter_prompt_is_stake_blind_and_fenced():
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a, b = _pending(m, vm, creator, alice, bob, alice_stake=777777, bob_stake=666666)
        _challenge(m, vm, carol, b, bond=424242)
        vm.clear_mocks()
        mock_sources(vm)
        cap = capture_llm(vm, wrapped({"preferred_id": a, "confidence": "0.9"}))
        vm.sender = alice
        m.resolve_challenge("1")
        prompt = cap.prompts[0]
        for forbidden in ("777777", "666666", "424242", "backers", "total_stake", key(carol)[2:]):
            assert forbidden not in prompt
        for tag in ("INTERPRETATION_TYPE", "INTERPRETATIONS", "LIVE_EVIDENCE"):
            assert prompt.count(f"<{tag}>") == 1


def test_challenge_validator_agrees_and_disagrees():
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a, b = _pending(m, vm, creator, alice, bob)
        _challenge(m, vm, carol, b)
        _resolve(m, vm, alice, b, confidence="0.9")
        assert vm.run_validator() is True
        rec = m.get_round_info("1")["challenge"]
        leader = {
            "decision": "decided",
            "preferred_id": a,
            "confidence": "0.9",
            "reasoning": "",
            "evidence_snapshot": rec["evidence_snapshot"],
            "evidence_hash": rec["evidence_hash"],
        }
        assert vm.run_validator(leader_result=leader) is False
        leader["preferred_id"] = b
        leader["confidence"] = "0.5"
        assert vm.run_validator(leader_result=leader) is False


def test_challenge_bond_is_isolated_per_round():
    """Round 1's challenge bond must not be claimable from round 2."""
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a, b = _pending(m, vm, creator, alice, bob)
        _challenge(m, vm, carol, b, bond=20)
        _resolve(m, vm, alice, a)  # rejected -> bond into round 1 pot
        submit(m, vm, carol, "Round two.", claims("Two"), 10)
        vm.clear_mocks()
        vm.sender = creator
        m.adjudicate()  # inconclusive
        assert m.get_claimable("2", to_hex(carol)) == "10"
        buckets = m.get_vault_state()["buckets"]
        assert buckets["round:1"] == "220"
        assert buckets["round:2"] == "10"
