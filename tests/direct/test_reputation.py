"""
Reputation. Two layers:
  * local ledger inside each Monocle, updated at FINAL decisions;
  * MonocleReputation, a separate pull-based aggregator that only performs
    synchronous cross-contract VIEWS. Here those views are stubbed through
    the gl_call hook (test_system_glsim.py runs them against real contracts).
Reputation is an output only: it never reaches an adjudication prompt.
"""

from gltest.direct import VMContext, create_test_addresses

from conftest import (
    ARBITER_RE,
    REPUTATION_PATH,
    capture_llm,
    claims,
    decide,
    deploy,
    deploy_monocle,
    finalize_after_window,
    install_recorder,
    key,
    mock_sources,
    submit,
    to_hex,
    verdict,
    wrapped,
)

MONOCLE = "0x" + "ab" * 20


# ----------------------------------------------------------------------
# Local ledger
# ----------------------------------------------------------------------


def test_local_reputation_records_wins_and_losses_only_at_finalize():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        submit(m, vm, bob, "Falling.", claims("It fell"), 10)
        decide(m, vm, creator, a)
        assert m.get_reputation(to_hex(alice))["decided_wins"] == 0  # pending is not final
        finalize_after_window(m, vm)
        ra, rb = m.get_reputation(to_hex(alice)), m.get_reputation(to_hex(bob))
        assert ra["decided_wins"] == 1 and ra["decided_losses"] == 0
        assert rb["decided_wins"] == 0 and rb["decided_losses"] == 1
        assert int(ra["last_finalized_at"]) > 0


def test_local_reputation_counts_inconclusive_participation():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        vm.clear_mocks()
        vm.sender = creator
        m.adjudicate()
        assert m.get_reputation(to_hex(alice))["inconclusive_participations"] == 1


def test_local_reputation_tracks_challenges():
    vm = VMContext()
    creator, alice, bob, carol = create_test_addresses(4)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        b = submit(m, vm, bob, "Falling.", claims("It fell"), 10)
        decide(m, vm, creator, a)
        vm.sender = carol
        vm.value = 20
        m.challenge("1", b)
        vm.value = 0
        vm.clear_mocks()
        mock_sources(vm)
        vm.mock_llm(ARBITER_RE, wrapped({"preferred_id": b, "confidence": "0.9"}))
        m.resolve_challenge("1")
        assert m.get_reputation(to_hex(carol))["challenges_won"] == 1
        assert m.get_reputation(to_hex(bob))["decided_wins"] == 1
        assert m.get_reputation(to_hex(alice))["decided_losses"] == 1


def test_reputation_is_never_an_adjudication_input():
    """A long winning streak must not appear in, or influence, the prompt."""
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        decide(m, vm, creator, a)
        finalize_after_window(m, vm)
        b = submit(m, vm, bob, "Something new.", claims("Fees fell"), 10)
        vm.clear_mocks()
        mock_sources(vm, "Evidence A: fees fell.", "Evidence B: fees fell.")
        cap = capture_llm(vm, verdict(b))
        vm.sender = creator
        m.adjudicate()
        prompt = cap.prompts[0]
        for forbidden in ("decided_wins", "reputation", key(alice)[2:], key(bob)[2:]):
            assert forbidden not in prompt


def test_get_round_outcome_is_stake_free_and_flags_finality():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Rising.", claims("It rose"), 12345)
        decide(m, vm, creator, a)
        assert m.get_round_outcome("1")["final"] is False
        finalize_after_window(m, vm)
        out = m.get_round_outcome("1")
        assert out["final"] is True
        assert out["winner_id"] == a
        assert out["authors"] == [{"id": a, "author": key(alice)}]
        assert "12345" not in str(out)


# ----------------------------------------------------------------------
# MonocleReputation aggregator (views stubbed)
# ----------------------------------------------------------------------


def _rep(vm, factory):
    vm.sender = factory
    return deploy(vm, REPUTATION_PATH)


def _stub(rec, factory, registered=True, outcome=None):
    rec.view_stubs[(key(factory), "is_registered")] = registered
    rec.view_stubs[(MONOCLE, "get_round_outcome")] = outcome


def _final(winner="1-0", status="finalized", authors=None, challenger="", challenge_outcome=""):
    return {
        "round": "1",
        "status": status,
        "final": True,
        "winner_id": winner,
        "authors": authors or [],
        "challenger": challenger,
        "challenge_outcome": challenge_outcome,
        "finalized_at": "1700000000",
    }


def test_record_round_applies_wins_losses_and_challenges():
    vm = VMContext()
    factory, alice, bob, carol, caller = create_test_addresses(5)
    with vm.activate():
        rec = install_recorder(vm)
        r = _rep(vm, factory)
        assert r.get_factory().lower() == key(factory)
        _stub(rec, factory, outcome=_final(
            authors=[{"id": "1-0", "author": key(alice)}, {"id": "1-1", "author": key(bob)}],
            challenger=key(carol), challenge_outcome="rejected",
        ))
        vm.sender = caller  # permissionless
        applied = r.record_round(MONOCLE, "1")
        assert applied["wins"] == [key(alice)] and applied["losses"] == [key(bob)]
        assert applied["challenge"] == "lost"
        ra = r.get_reputation(to_hex(alice))
        assert ra["decided_wins"] == 1 and ra["rounds_recorded"] == 1
        assert ra["last_finalized_at"] == "1700000000"
        assert r.get_reputation(to_hex(bob))["decided_losses"] == 1
        assert r.get_reputation(to_hex(carol))["challenges_lost"] == 1
        assert r.is_recorded(MONOCLE.upper().replace("0X", "0x"), "1") is True
        assert r.get_recorded_page(0, 10)["keys"] == [f"{MONOCLE}:1"]
        # The registry and the Monocle were both consulted, views only.
        assert [v[1] for v in rec.views] == ["is_registered", "get_round_outcome"]
        assert rec.messages == []


def test_record_round_is_idempotent():
    vm = VMContext()
    factory, alice = create_test_addresses(2)
    with vm.activate():
        rec = install_recorder(vm)
        r = _rep(vm, factory)
        _stub(rec, factory, outcome=_final(authors=[{"id": "1-0", "author": key(alice)}]))
        r.record_round(MONOCLE, "1")
        with vm.expect_revert("already recorded"):
            r.record_round(MONOCLE, "1")
        assert r.get_reputation(to_hex(alice))["decided_wins"] == 1


def test_record_round_rejects_unregistered_monocle():
    vm = VMContext()
    factory, = create_test_addresses(1)
    with vm.activate():
        rec = install_recorder(vm)
        r = _rep(vm, factory)
        _stub(rec, factory, registered=False, outcome=_final())
        with vm.expect_revert("not registered"):
            r.record_round(MONOCLE, "1")


def test_record_round_rejects_non_final_round():
    vm = VMContext()
    factory, = create_test_addresses(1)
    with vm.activate():
        rec = install_recorder(vm)
        r = _rep(vm, factory)
        pending = _final()
        pending["final"] = False
        pending["status"] = "decided_pending"
        _stub(rec, factory, outcome=pending)
        with vm.expect_revert("not final"):
            r.record_round(MONOCLE, "1")


def test_record_round_inconclusive_and_cancelled():
    vm = VMContext()
    factory, alice = create_test_addresses(2)
    with vm.activate():
        rec = install_recorder(vm)
        r = _rep(vm, factory)
        _stub(rec, factory, outcome=_final(status="inconclusive", winner="", authors=[{"id": "1-0", "author": key(alice)}]))
        r.record_round(MONOCLE, "1")
        assert r.get_reputation(to_hex(alice))["inconclusive_participations"] == 1
        cancelled = _final(status="cancelled", winner="", authors=[{"id": "2-0", "author": key(alice)}])
        cancelled["round"] = "2"
        rec.view_stubs[(MONOCLE, "get_round_outcome")] = cancelled
        applied = r.record_round(MONOCLE, "2")
        assert applied["wins"] == [] and applied["inconclusive"] == []
        rep = r.get_reputation(to_hex(alice))
        assert rep["inconclusive_participations"] == 1
        assert rep["decided_wins"] == 0


def test_unknown_address_has_empty_reputation():
    vm = VMContext()
    factory, nobody = create_test_addresses(2)
    with vm.activate():
        r = _rep(vm, factory)
        rep = r.get_reputation(to_hex(nobody))
        assert rep["decided_wins"] == 0 and rep["rounds_recorded"] == 0
        with vm.expect_revert("not recorded"):
            r.get_recorded_round(MONOCLE, "1")
