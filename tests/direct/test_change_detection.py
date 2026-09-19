"""
Change detection: evidence is content-hashed in contract code. When the
evidence hash equals the last FINAL snapshot and nothing new was submitted,
adjudication short-circuits to "unchanged" (no model call, full refund,
live output kept). Anything genuinely new is re-judged.
"""

from gltest.direct import VMContext, create_test_addresses

from conftest import (
    ADJUDICATOR_RE,
    capture_llm,
    claims,
    decide,
    deploy_monocle,
    finalize_after_window,
    install_recorder,
    mock_sources,
    submit,
    to_hex,
    verdict,
)

BODY_A = "Evidence A: dominance climbed on ETF inflows."
BODY_B = "Evidence B: dominance climbed on ETF inflows."


def _live_round_one(m, vm, creator, alice):
    a = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
    decide(m, vm, creator, a)
    finalize_after_window(m, vm)
    return a


def _adjudicate_capturing(m, vm, caller, body_a=BODY_A, body_b=BODY_B, response=None):
    vm.clear_mocks()
    mock_sources(vm, body_a, body_b)
    cap = capture_llm(vm, response or "{}")
    vm.sender = caller
    out = m.adjudicate()
    return out, cap


def test_unchanged_evidence_and_nothing_new_short_circuits_without_llm():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        a = _live_round_one(m, vm, creator, alice)
        # Round 2: the very same interpretation is resubmitted.
        submit(m, vm, alice, "Rising.", claims("It rose"), 25)
        out, cap = _adjudicate_capturing(m, vm, creator)
        assert out == ""
        assert cap.prompts == []
        r2 = m.get_round_info("2")
        assert r2["status"] == "unchanged"
        assert r2["reasoning"]["decision"] == "unchanged"
        assert r2["finality"] == "refund"
        live = m.get_live_interpretation()
        assert live["interpretation"]["id"] == a
        assert live["finality"] == "final"
        assert m.get_current_round() == "3"
        vm.sender = alice
        assert m.claim("2") == "25"
        assert rec.transfers_to(alice) == 25


def test_new_interpretation_with_unchanged_evidence_is_rejudged():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _live_round_one(m, vm, creator, alice)
        b = submit(m, vm, bob, "Rising, and fees fell.", claims("It rose", "Fees fell"), 10)
        out, cap = _adjudicate_capturing(m, vm, creator, response=verdict(b))
        assert len(cap.prompts) == 1
        assert out == b


def test_changed_evidence_is_rejudged():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _live_round_one(m, vm, creator, alice)
        a2 = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        out, cap = _adjudicate_capturing(m, vm, creator, body_a="Evidence A: dominance fell sharply today.",
                                         response=verdict(a2, confidence="0.3"))
        assert len(cap.prompts) == 1
        assert m.get_round_info("2")["status"] == "inconclusive"


def test_no_live_output_means_never_unchanged():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        decide(m, vm, creator, a, confidence="0.3")  # inconclusive, no live
        a2 = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        out, cap = _adjudicate_capturing(m, vm, creator, response=verdict(a2))
        assert len(cap.prompts) == 1


def test_evidence_hash_detects_change_hidden_by_sanitizer():
    """Braces are stripped from excerpts, so "value {1}" and "value 1" have
    identical excerpts -- but the hash is taken over RAW fetched text, so the
    change is still detected (no unchanged-hash false negative)."""
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        vm.clear_mocks()
        mock_sources(vm, "Index value 1 today.", "Index value 1 confirmed.")
        vm.mock_llm(ADJUDICATOR_RE, verdict(a))
        vm.sender = creator
        m.adjudicate()
        finalize_after_window(m, vm)
        h1 = m.get_evidence_snapshot("1")["evidence_hash"]
        submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        _, cap = _adjudicate_capturing(m, vm, creator, "Index value {1} today.", "Index value 1 confirmed.",
                                       response=verdict("2-1", confidence="0.3"))
        snap = m.get_evidence_snapshot("2")
        assert snap["evidence_snapshot"][0]["excerpt"] == "Index value 1 today."
        assert snap["evidence_hash"] != h1
        assert len(cap.prompts) == 1


def test_evidence_hash_detects_change_beyond_excerpt_truncation():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        prefix = "x" * 2500
        a = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        vm.clear_mocks()
        mock_sources(vm, prefix + " tail one", prefix + " tail B")
        vm.mock_llm(ADJUDICATOR_RE, verdict(a))
        vm.sender = creator
        m.adjudicate()
        finalize_after_window(m, vm)
        submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        _, cap = _adjudicate_capturing(m, vm, creator, prefix + " tail two", prefix + " tail B",
                                       response=verdict("2-1", confidence="0.3"))
        assert len(cap.prompts) == 1  # re-judged, not "unchanged"


def test_whitespace_only_change_is_treated_as_unchanged():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _live_round_one(m, vm, creator, alice)
        submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        _, cap = _adjudicate_capturing(m, vm, creator, BODY_A.replace(" ", "   "), BODY_B + "\n\n")
        assert cap.prompts == []
        assert m.get_round_info("2")["status"] == "unchanged"


def test_evidence_hash_is_independent_of_source_order():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        sys_mod = __import__("sys")
        deploy_monocle(vm, creator)
        mod = sys_mod.modules["_contract_Monocle"]
        f1 = [{"url": "https://a", "ok": True, "content_hash": "h1"}, {"url": "https://b", "ok": True, "content_hash": "h2"}]
        assert mod._evidence_hash(f1) == mod._evidence_hash(list(reversed(f1)))
        assert mod._evidence_hash([{"url": "https://a", "ok": False, "content_hash": ""}]) == ""


def test_validator_disagrees_with_unchanged_leader_when_its_fetch_differs():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _live_round_one(m, vm, creator, alice)
        submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        _adjudicate_capturing(m, vm, creator)
        assert vm.run_validator() is True
        vm.clear_mocks()
        mock_sources(vm, "Evidence A: something new happened.", BODY_B)
        vm.mock_llm(ADJUDICATOR_RE, verdict("2-1"))
        assert vm.run_validator() is False


def test_validator_rejudges_a_decided_leader_even_if_its_own_fetch_matches_prior():
    """If the leader chose to judge, a validator whose fetch equals the prior
    snapshot must not short-circuit to 'unchanged' (which would disagree);
    it re-reasons and compares verdicts."""
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _live_round_one(m, vm, creator, alice)
        b = submit(m, vm, bob, "Rising and fees fell.", claims("It rose", "Fees fell"), 10)
        vm.clear_mocks()
        mock_sources(vm, BODY_A, BODY_B)
        vm.mock_llm(ADJUDICATOR_RE, verdict(b))
        vm.sender = creator
        assert m.adjudicate() == b
        assert vm.run_validator() is True
