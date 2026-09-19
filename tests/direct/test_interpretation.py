"""submit_interpretation / back_interpretation: bond floor, duplicate-hash
rejection, schema enforcement and the claim model."""

import json

from gltest.direct import VMContext, create_test_addresses

from conftest import back, claims, decide, deploy_monocle, key, submit, to_hex


def test_submit_creates_record_pool_and_claims():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        iid = submit(
            m,
            vm,
            alice,
            "Dominance is trending up on ETF inflows.",
            json.dumps(
                {
                    "claims": [{"statement": "BTC dominance rose 2%", "entities": ["BTC"]}, "ETF inflows drove it"],
                    "direction": "up",
                }
            ),
            100,
        )
        assert iid == "1-0"
        rec = m.get_interpretation(iid)
        assert rec["content"] == "Dominance is trending up on ETF inflows."
        assert rec["fields"]["direction"] == "up"
        assert [c["id"] for c in rec["claims"]] == ["c0", "c1"]
        assert rec["claims"][0]["entities"] == ["BTC"]
        assert rec["claims"][0]["core"] is True
        assert rec["claims"][1]["core"] is False
        assert rec["stance"] == "assertion"
        assert rec["total_stake"] == "100"
        assert rec["backer_count"] == 1
        assert rec["author"].lower() == key(alice)
        r = m.get_round_info("1")
        assert r["pool"] == "100"
        assert r["interpretation_ids"] == ["1-0"]
        info = m.get_monocle_info()
        assert info["total_stake_all_time"] == "100"
        assert info["interpretation_count"] == "1"


def test_flat_field_claims_become_claims():
    """A flat structured-claims object is accepted; each scalar field
    becomes a comparable claim."""
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        iid = submit(m, vm, alice, "Up.", json.dumps({"direction": "up", "driver": "etf"}), 10)
        statements = [c["statement"] for c in m.get_interpretation(iid)["claims"]]
        assert statements == ["direction = up", "driver = etf"]


def test_empty_claims_fall_back_to_content_claim():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        iid = submit(m, vm, alice, "The page confirms X.", "", 10)
        assert m.get_interpretation(iid)["claims"][0]["statement"] == "The page confirms X."


def test_explicit_core_flag_and_refutation_stance():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        iid = submit(
            m,
            vm,
            alice,
            "The rumour is false.",
            json.dumps({"claims": ["Context", {"statement": "No merger happened", "core": True}], "stance": "refutation"}),
            10,
        )
        rec = m.get_interpretation(iid)
        assert rec["stance"] == "refutation"
        assert [c["core"] for c in rec["claims"]] == [False, True]


def test_submit_requires_minimum_bond():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        with vm.expect_revert("bond too low"):
            submit(m, vm, alice, "Content.", "", 9)
        with vm.expect_revert("bond too low"):
            submit(m, vm, alice, "Content.", "", 0)


def test_submit_requires_content():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        with vm.expect_revert("content is required"):
            submit(m, vm, alice, "   ", "", 10)


def test_submit_rejects_invalid_json_claims():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        with vm.expect_revert("valid JSON"):
            submit(m, vm, alice, "Content.", "{not json", 10)


def test_submit_rejects_non_object_claims():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        with vm.expect_revert("JSON object"):
            submit(m, vm, alice, "Content.", json.dumps(["a", "b"]), 10)


def test_submit_rejects_too_many_claims_and_empty_claim_list():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        with vm.expect_revert("At most 12 claims"):
            submit(m, vm, alice, "Content.", claims(*[f"c{i}" for i in range(13)]), 10)
        with vm.expect_revert("at least one non-empty claim"):
            submit(m, vm, alice, "Content.", json.dumps({"claims": ["", "  "]}), 10)


def test_submit_rejects_oversized_claims_blob():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        with vm.expect_revert("exceeds max length"):
            submit(m, vm, alice, "Content.", json.dumps({"claims": ["x" * 4100]}), 10)


def test_schema_fields_are_required():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator, schema_json=json.dumps({"direction": "up|down", "magnitude": "percent"}))
        with vm.expect_revert("missing schema fields: magnitude"):
            submit(m, vm, alice, "Up.", claims("It rose", direction="up"), 10)
        iid = submit(m, vm, alice, "Up.", claims("It rose", direction="up", magnitude="2%"), 10)
        assert m.get_interpretation(iid)["fields"] == {"direction": "up", "magnitude": "2%"}


def test_duplicate_interpretation_rejected_by_content_hash():
    """Anti-spam: the same content+claims (modulo case/whitespace) cannot be
    submitted twice in one round, even by a different address."""
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        submit(m, vm, alice, "BTC dominance is rising.", claims("It rose"), 10)
        with vm.expect_revert("duplicate"):
            submit(m, vm, bob, "  btc   DOMINANCE is rising. ", claims("it ROSE"), 10)
        # A genuinely different claim set is fine.
        submit(m, vm, bob, "BTC dominance is rising.", claims("It rose 3%"), 10)


def test_same_interpretation_allowed_again_in_a_later_round():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        submit(m, vm, alice, "Same read.", claims("Same"), 10)
        vm.sender = creator
        m.adjudicate()  # no mocks -> inconclusive -> round 2 opens
        assert m.get_current_round() == "2"
        submit(m, vm, alice, "Same read.", claims("Same"), 10)


def test_submit_deep_sanitizes_float_claims():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        iid = submit(m, vm, alice, "Content.", json.dumps({"confidence": 0.87, "ratio": 1.5}), 10)
        fields = m.get_interpretation(iid)["fields"]
        assert fields == {"confidence": "0.87", "ratio": "1.5"}


def test_max_interpretations_per_round_enforced():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        for i in range(12):
            submit(m, vm, alice, f"Interpretation {i}", "", 10)
        with vm.expect_revert("maximum"):
            submit(m, vm, alice, "One too many", "", 10)


def test_back_adds_backer_and_stake():
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        iid = submit(m, vm, alice, "Content.", "", 100)
        back(m, vm, bob, iid, 50)
        rec = m.get_interpretation(iid)
        assert rec["total_stake"] == "150"
        assert rec["backer_count"] == 2
        assert m.get_backing("1", iid, to_hex(alice)) == "100"
        assert m.get_backing("1", iid, to_hex(bob)) == "50"
        assert m.get_round_info("1")["pool"] == "150"


def test_back_accumulates_same_backer():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        iid = submit(m, vm, alice, "Content.", "", 100)
        back(m, vm, alice, iid, 25)
        assert m.get_backing("1", iid, to_hex(alice)) == "125"


def test_back_rejects_unknown_id_and_zero_value():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        with vm.expect_revert("not found"):
            back(m, vm, alice, "99-99", 10)
        iid = submit(m, vm, alice, "Content.", "", 10)
        with vm.expect_revert("Must send GEN"):
            back(m, vm, alice, iid, 0)


def test_submit_and_back_only_in_open_round():
    """While the round is decided_pending (challenge window), no new capital
    may enter it."""
    vm = VMContext()
    creator, alice, bob = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        iid = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        decide(m, vm, creator, iid)
        assert m.get_round_info("1")["status"] == "decided_pending"
        with vm.expect_revert("not open"):
            submit(m, vm, bob, "Late.", "", 10)
        with vm.expect_revert("not open"):
            back(m, vm, bob, iid, 10)


def test_back_rejects_interpretation_from_previous_round():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        iid = submit(m, vm, alice, "Old.", "", 10)
        vm.sender = creator
        m.adjudicate()  # inconclusive, round 2 opens
        with vm.expect_revert("current open round"):
            back(m, vm, alice, iid, 10)


def test_submit_rejected_while_closing():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        vm.sender = creator
        m.close_monocle()
        with vm.expect_revert("closing"):
            submit(m, vm, alice, "Content.", "", 10)
