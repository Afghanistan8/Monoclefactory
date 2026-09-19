"""Monocle.__init__ validation: sources, metadata, schema, bond config,
explicit creator, 8-source cap."""

import json

from gltest.direct import VMContext, create_test_addresses

from conftest import BONDS, SOURCES, deploy_monocle, key, to_hex


def test_valid_monocle_deploys_active_and_open():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator, title="BTC Dominance Trend", description="Tracks BTC dominance.")
        info = m.get_monocle_info()
        assert info["status"] == "active"
        assert info["current_round"] == "1"
        assert info["current_round_status"] == "open"
        assert info["sources"] == SOURCES
        assert info["interpretation_type"] == "market"
        assert info["title"] == "BTC Dominance Trend"
        assert info["creator"].lower() == key(creator)
        assert info["live_interpretation_id"] == ""
        assert info["interpretation_count"] == "0"
        assert info["min_interpretation_bond"] == str(BONDS[0])
        assert info["min_source_bond"] == str(BONDS[1])
        assert info["min_challenge_bond"] == str(BONDS[2])
        assert info["deployed_by_factory"] is False


def test_requires_at_least_two_sources():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        with vm.expect_revert("At least 2 sources"):
            deploy_monocle(vm, creator, sources=["https://example.com/feed"])


def test_rejects_empty_sources():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        with vm.expect_revert("At least 2 sources"):
            deploy_monocle(vm, creator, sources=[])


def test_accepts_eight_sources_rejects_nine():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator, sources=[f"https://example.com/{i}" for i in range(8)])
        assert len(m.get_sources()) == 8
        with vm.expect_revert("At most 8"):
            deploy_monocle(vm, creator, sources=[f"https://example.com/{i}" for i in range(9)])


def test_rejects_non_http_source():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        with vm.expect_revert("http(s)"):
            deploy_monocle(vm, creator, sources=["https://example.com/feed", "ftp://example.com/feed"])


def test_rejects_overlong_url():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        with vm.expect_revert("http(s)"):
            deploy_monocle(vm, creator, sources=["https://example.com/feed", "https://example.org/" + "a" * 600])


def test_rejects_duplicate_sources_after_normalization():
    """Trailing slash and host case must not create a 'second' source."""
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        with vm.expect_revert("unique"):
            deploy_monocle(vm, creator, sources=["https://Example.com/feed/", "https://example.com/feed"])


def test_requires_interpretation_type():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        with vm.expect_revert("interpretation_type"):
            deploy_monocle(vm, creator, interpretation_type="")


def test_requires_title():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        with vm.expect_revert("Title"):
            deploy_monocle(vm, creator, title="")


def test_description_is_optional():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator, description="")
        assert m.get_monocle_info()["description"] == ""


def test_round_one_starts_open_with_opened_at_recorded():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        r = m.get_round_info("1")
        assert r["status"] == "open"
        assert r["pool"] == "0"
        assert r["interpretation_ids"] == []
        assert int(r["opened_at"]) > 0
        assert r["finality"] == "none"


def test_schema_json_must_be_object():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        with vm.expect_revert("JSON object"):
            deploy_monocle(vm, creator, schema_json='["direction"]')
        with vm.expect_revert("valid JSON"):
            deploy_monocle(vm, creator, schema_json="{nope")


def test_schema_json_is_stored_sanitized():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        m = deploy_monocle(vm, creator, schema_json=json.dumps({"direction": "up|down", "driver": "short cause"}))
        assert m.get_monocle_info()["schema"] == {"direction": "up|down", "driver": "short cause"}


def test_bonds_must_be_positive():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        with vm.expect_revert("min_challenge_bond"):
            deploy_monocle(vm, creator, bonds=(10, 5, 0))


def test_explicit_creator_overrides_deployer():
    """msg.sender is the FACTORY for factory-made Monocles; the real creator
    must be passed explicitly or close_monocle would be unreachable."""
    vm = VMContext()
    factory_like, real_creator = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, factory_like, creator_arg=to_hex(real_creator))
        info = m.get_monocle_info()
        assert info["creator"].lower() == key(real_creator)
        assert info["factory_address"].lower() == key(factory_like)
        assert info["deployed_by_factory"] is True
        vm.sender = real_creator
        m.close_monocle()
        assert m.get_monocle_info()["status"] == "closing"


def test_rejects_malformed_creator_address():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        with vm.expect_revert("creator must be"):
            deploy_monocle(vm, creator, creator_arg="not-an-address")
