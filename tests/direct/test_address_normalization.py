"""Address normalization: every address-keyed lookup and owner/creator check
must be insensitive to EIP-55 casing (a common GenLayer failure class:
silent "not found" on a real stake record)."""

import sys

from gltest.direct import VMContext, create_test_addresses

from conftest import FACTORY_PATH, MONOCLE_PATH, claims, decide, deploy, deploy_monocle, finalize_after_window, submit, to_hex


def _variants(addr):
    checksummed = to_hex(addr)
    return (checksummed, checksummed.lower(), "0x" + checksummed[2:].upper(), "  " + checksummed + "  ")


def test_backing_lookup_is_case_insensitive():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        iid = submit(m, vm, alice, "Content.", "", 42)
        for lookup in _variants(alice):
            assert m.get_backing("1", iid, lookup) == "42", lookup


def test_claimable_and_is_claimed_are_case_insensitive():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Content.", claims("X"), 42)
        decide(m, vm, creator, a)
        finalize_after_window(m, vm)
        m.settle("1")
        for lookup in _variants(alice):
            assert m.get_claimable("1", lookup) == "42"
        vm.sender = alice
        m.claim("1")
        for lookup in _variants(alice):
            assert m.is_claimed("1", lookup) is True
            assert m.get_claimable("1", lookup) == "0"


def test_reputation_lookup_is_case_insensitive():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        a = submit(m, vm, alice, "Content.", claims("X"), 42)
        decide(m, vm, creator, a)
        finalize_after_window(m, vm)
        for lookup in _variants(alice):
            assert m.get_reputation(lookup)["decided_wins"] == 1


def test_creator_check_uses_normalized_addresses_with_lowercase_creator_arg():
    vm = VMContext()
    factory_like, creator = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, factory_like, creator_arg=to_hex(creator).lower())
        vm.sender = creator
        m.close_monocle()
        assert m.get_monocle_info()["status"] == "closing"


def test_normalize_address_helper_accepts_address_and_str():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        deploy_monocle(vm, creator)
        mod = sys.modules["_contract_Monocle"]
        addr = creator if hasattr(creator, "as_hex") else mod.Address(creator)
        assert mod._normalize_address(addr) == to_hex(creator).lower()
        assert mod._normalize_address("  0xABCdef  ") == "0xabcdef"
        assert mod._normalize_address(None) == ""


def test_factory_creator_filter_and_owner_check_are_case_insensitive():
    vm = VMContext()
    owner, alice = create_test_addresses(2)
    with vm.activate():
        vm.sender = owner
        f = deploy(vm, FACTORY_PATH, MONOCLE_PATH.read_text(encoding="utf-8"), "", 0, 10, 5, 20, 3600)
        vm.sender = alice
        addr = f.create_monocle(["https://example.com/a", "https://example.org/b"], "market", "T", "D", "")
        for lookup in _variants(alice):
            assert f.get_monocles_by_creator(lookup) == [addr]
        for lookup in (addr, addr.lower(), "0x" + addr[2:].upper()):
            assert f.get_monocle_meta(lookup)["address"] == addr
        assert f.get_owner().lower() == to_hex(owner).lower()
