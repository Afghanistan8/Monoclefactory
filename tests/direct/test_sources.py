"""add_source: permissionless, append-only, bonded, role-tagged; source bond
refund on fetchable evidence and forfeiture after repeated misses."""

from gltest.direct import VMContext, create_test_addresses

from conftest import (
    ADJUDICATOR_RE,
    SOURCES,
    advance,
    claims,
    decide,
    deploy_monocle,
    finalize_after_window,
    install_recorder,
    key,
    mock_sources,
    submit,
    verdict,
    web,
)


def _add(m, vm, sender, url, role="corroborating", value=5):
    vm.sender = sender
    vm.value = value
    try:
        return m.add_source(url, role)
    finally:
        vm.value = 0


def test_add_source_is_permissionless_and_records_provenance():
    vm = VMContext()
    creator, outsider = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _add(m, vm, outsider, "https://example.net/independent", role="contradicting")
        srcs = m.get_sources()
        assert len(srcs) == 3
        new = srcs[2]
        assert new["url"] == "https://example.net/independent"
        assert new["role"] == "contradicting"
        assert new["added_by"].lower() == key(outsider)
        assert new["add_bond"] == "5"
        assert new["bond_status"] == "locked"
        assert new["last_fetch_ok"] is False


def test_add_source_requires_bond():
    vm = VMContext()
    creator, outsider = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        with vm.expect_revert("Source bond too low"):
            _add(m, vm, outsider, "https://example.net/x", value=4)


def test_add_source_rejects_bad_role():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        with vm.expect_revert("role must be"):
            _add(m, vm, creator, "https://example.net/x", role="oracle")


def test_add_source_defaults_role_to_corroborating():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _add(m, vm, creator, "https://example.net/x", role="")
        assert m.get_sources()[2]["role"] == "corroborating"


def test_add_source_rejects_duplicate_including_normalized_variants():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        for dup in (SOURCES[0], "https://EXAMPLE.com/feed/", " https://example.com/feed#frag "):
            with vm.expect_revert("already added"):
                _add(m, vm, creator, dup)


def test_add_source_keeps_path_case():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _add(m, vm, creator, "https://example.com/Feed")
        assert m.get_sources()[2]["url"] == "https://example.com/Feed"


def test_add_source_rejects_bad_url():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        with vm.expect_revert("http(s)"):
            _add(m, vm, creator, "not-a-url")


def test_add_source_enforces_max_sources():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        for i in range(6):
            _add(m, vm, creator, f"https://example.net/{i}")
        assert len(m.get_sources()) == 8
        with vm.expect_revert("maximum"):
            _add(m, vm, creator, "https://example.net/overflow")


def test_add_source_rejected_while_closing_and_after_close():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        vm.sender = creator
        m.close_monocle()
        with vm.expect_revert("closing"):
            _add(m, vm, creator, "https://example.net/late")
        advance(vm, 7201)
        m.finalize_close()
        with vm.expect_revert("Monocle is closed"):
            _add(m, vm, creator, "https://example.net/late")


def test_there_is_no_remove_source_method():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        for name in ("remove_source", "delete_source", "set_sources", "replace_source"):
            assert not hasattr(m, name)


def test_source_bond_becomes_refundable_after_fetchable_evidence():
    vm = VMContext()
    creator, alice, adder = create_test_addresses(3)
    with vm.activate():
        rec = install_recorder(vm)
        m = deploy_monocle(vm, creator)
        _add(m, vm, adder, "https://example.net/third")
        a = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        vm.clear_mocks()
        mock_sources(vm)
        vm.mock_web(r"example\.net/third", web("Third source agrees it rose."))
        vm.mock_llm(ADJUDICATOR_RE, verdict(a))
        vm.sender = creator
        m.adjudicate()
        third = m.get_sources()[2]
        assert third["bond_status"] == "refundable"
        assert third["last_fetch_ok"] is True
        assert third["last_content_hash"] != ""

        vm.sender = creator
        with vm.expect_revert("Only the address that added"):
            m.claim_source_bond("https://example.net/third")
        vm.sender = adder
        m.claim_source_bond("https://example.net/third")
        assert rec.transfers_to(adder) == 5
        assert m.get_sources()[2]["bond_status"] == "refunded"
        with vm.expect_revert("not refundable"):
            m.claim_source_bond("https://example.net/third")


def test_locked_source_bond_is_not_claimable():
    vm = VMContext()
    creator, adder = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _add(m, vm, adder, "https://example.net/third")
        vm.sender = adder
        with vm.expect_revert("not refundable"):
            m.claim_source_bond("https://example.net/third")


def test_unfetchable_source_forfeits_bond_after_repeated_misses_into_next_pot():
    """A bonded source that keeps failing while other sources fetch fine
    forfeits its bond after 3 adjudications; the forfeit joins the next
    FINAL pot (paid to winners) instead of stranding."""
    vm = VMContext()
    creator, alice, adder = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _add(m, vm, adder, "https://example.net/dead", value=7)
        for i in range(3):
            iid = submit(m, vm, alice, f"Read {i}.", claims(f"Claim {i}"), 10)
            # Low confidence -> inconclusive, but the fetch pass still counts.
            decide(m, vm, creator, iid, confidence="0.3")
        dead = m.get_sources()[2]
        assert dead["misses"] == 3
        assert dead["bond_status"] == "forfeited"
        assert m.get_vault_state()["buckets"]["carry"] == "7"

        iid = submit(m, vm, alice, "Final read.", claims("Final claim"), 10)
        decide(m, vm, creator, iid)
        finalize_after_window(m, vm, "4")
        r4 = m.get_round_info("4")
        assert r4["bonus"] == "7"
        assert r4["pot"] == "17"


def test_global_outage_never_burns_a_source_bond():
    vm = VMContext()
    creator, alice, adder = create_test_addresses(3)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        _add(m, vm, adder, "https://example.net/third")
        for i in range(4):
            submit(m, vm, alice, f"Read {i}.", claims(f"Claim {i}"), 10)
            vm.clear_mocks()  # nothing fetches at all
            vm.sender = creator
            m.adjudicate()
        third = m.get_sources()[2]
        assert third["misses"] == 0
        assert third["bond_status"] == "locked"
