"""
MonocleFactory in direct mode. gl.contract.deploy is a CREATE2 message: the
address is derived locally (deployer, salt, chain) and the deploy message is
captured by the recorder, so create_monocle's full success path -- registry,
metadata, fee accounting and excess refund -- is testable here. The child
actually executing its constructor is covered in test_system_glsim.py.
"""

import json

from gltest.direct import VMContext, create_test_addresses

from conftest import FACTORY_PATH, MONOCLE_PATH, REPUTATION_PATH, deploy, install_recorder, key, to_hex

SOURCES = ["https://example.com/feed", "https://example.org/feed"]


def _factory(vm, owner, creation_stake=0, with_reputation=True, bonds=(10, 5, 20), window=3600):
    vm.sender = owner
    vm.value = 0
    rep_code = REPUTATION_PATH.read_text(encoding="utf-8") if with_reputation else ""
    return deploy(vm, FACTORY_PATH, MONOCLE_PATH.read_text(encoding="utf-8"), rep_code, creation_stake, *bonds, window)


def _create(f, vm, sender, value=0, sources=None, itype="market", title="Title", desc="Desc", schema=""):
    vm.sender = sender
    vm.value = value
    try:
        return f.create_monocle(list(SOURCES if sources is None else sources), itype, title, desc, schema)
    finally:
        vm.value = 0


def test_factory_deploys_with_owner_stake_bonds_and_reputation():
    vm = VMContext()
    owner, = create_test_addresses(1)
    with vm.activate():
        rec = install_recorder(vm)
        f = _factory(vm, owner, creation_stake=100)
        assert f.get_creation_stake() == "100"
        assert f.get_monocles_count() == 0
        assert f.get_collected_fees() == "0"
        assert f.get_residual_fees() == "0"
        assert f.get_owner().lower() == key(owner)
        assert f.get_bond_config() == {
            "min_interpretation_bond": "10",
            "min_source_bond": "5",
            "min_challenge_bond": "20",
            "challenge_window_seconds": "3600",
        }
        rep = f.get_reputation_address()
        assert rep.startswith("0x") and len(rep) == 42
        assert len(rec.deploys) == 1
        assert rec.deploys[0]["salt_nonce"] == 1
        assert f.get_vault_model()["model"] == "in_process"


def test_factory_without_reputation_code_has_empty_reputation_address():
    vm = VMContext()
    owner, = create_test_addresses(1)
    with vm.activate():
        f = _factory(vm, owner, with_reputation=False)
        assert f.get_reputation_address() == ""


def test_factory_requires_monocle_code_and_valid_config():
    vm = VMContext()
    owner, = create_test_addresses(1)
    with vm.activate():
        vm.sender = owner
        with vm.expect_revert("Missing Monocle contract source"):
            deploy(vm, FACTORY_PATH, "", "", 0, 10, 5, 20, 3600)
        with vm.expect_revert("creation_stake"):
            deploy(vm, FACTORY_PATH, "code", "", -1, 10, 5, 20, 3600)
        with vm.expect_revert("min_source_bond"):
            deploy(vm, FACTORY_PATH, "code", "", 0, 10, 0, 20, 3600)


def test_create_rejects_insufficient_stake():
    vm = VMContext()
    owner, alice = create_test_addresses(2)
    with vm.activate():
        f = _factory(vm, owner, creation_stake=100)
        with vm.expect_revert("stake too low"):
            _create(f, vm, alice, value=50)


def test_create_rejects_bad_source_sets():
    vm = VMContext()
    owner, alice = create_test_addresses(2)
    with vm.activate():
        f = _factory(vm, owner)
        with vm.expect_revert("At least 2 sources"):
            _create(f, vm, alice, sources=["https://example.com/feed"])
        with vm.expect_revert("At least 2 sources"):
            _create(f, vm, alice, sources=[])
        with vm.expect_revert("At most 8"):
            _create(f, vm, alice, sources=[f"https://example.com/{i}" for i in range(9)])
        with vm.expect_revert("http(s)"):
            _create(f, vm, alice, sources=["https://example.com/feed", "not-a-url"])


def test_create_rejects_missing_or_oversized_metadata():
    vm = VMContext()
    owner, alice = create_test_addresses(2)
    with vm.activate():
        f = _factory(vm, owner)
        with vm.expect_revert("interpretation_type"):
            _create(f, vm, alice, itype="")
        with vm.expect_revert("Title is required"):
            _create(f, vm, alice, title="")
        with vm.expect_revert("Description exceeds"):
            _create(f, vm, alice, desc="x" * 1001)
        with vm.expect_revert("schema_json exceeds"):
            _create(f, vm, alice, schema="x" * 2001)


def test_create_registers_metadata_and_deploys_child_with_real_creator():
    vm = VMContext()
    owner, alice = create_test_addresses(2)
    with vm.activate():
        rec = install_recorder(vm)
        f = _factory(vm, owner, creation_stake=100)
        rec.clear()
        addr = _create(f, vm, alice, value=100, schema=json.dumps({"direction": "up|down"}))
        assert addr.startswith("0x")
        assert f.get_monocles() == [addr]
        assert f.get_monocles_count() == 1
        assert f.is_registered(addr.lower()) is True
        meta = f.get_monocle_meta(addr)
        assert meta["title"] == "Title"
        assert meta["creator"].lower() == key(alice)
        assert meta["stake_required"] == "100" and meta["stake_paid"] == "100" and meta["excess_refunded"] == "0"
        assert meta["index"] == 0
        # Deploy message: args carry the REAL creator and the bond config.
        (dep,) = rec.deploys
        args = dep["calldata"]["args"]
        assert args[5:9] == [10, 5, 20, 3600]
        assert args[9].lower() == key(alice)
        assert dep["salt_nonce"] == 2  # salt 1 is the reputation contract
        assert addr.lower() != f.get_reputation_address().lower()


def test_factory_metadata_is_creation_time_only():
    vm = VMContext()
    owner, alice = create_test_addresses(2)
    with vm.activate():
        f = _factory(vm, owner)
        addr = _create(f, vm, alice)
        meta = f.get_monocle_meta(addr)
        for live_field in ("status", "current_round", "live_interpretation_id", "live_round"):
            assert live_field not in meta
        assert not any(name.startswith("get_live") for name in dir(f))


def test_collected_fees_count_exactly_creation_stake_and_excess_is_refunded():
    vm = VMContext()
    owner, alice, bob = create_test_addresses(3)
    with vm.activate():
        rec = install_recorder(vm)
        f = _factory(vm, owner, creation_stake=100)
        a1 = _create(f, vm, alice, value=250)
        _create(f, vm, bob, value=100)
        assert f.get_collected_fees() == "200"
        assert rec.transfers_to(alice) == 150
        assert rec.transfers_to(bob) == 0
        assert f.get_monocle_meta(a1)["excess_refunded"] == "150"


def test_distinct_salts_produce_distinct_addresses():
    vm = VMContext()
    owner, alice = create_test_addresses(2)
    with vm.activate():
        f = _factory(vm, owner)
        addrs = [_create(f, vm, alice, title=f"T{i}") for i in range(3)]
        assert len(set(a.lower() for a in addrs)) == 3


def test_pagination_bounds():
    vm = VMContext()
    owner, alice = create_test_addresses(2)
    with vm.activate():
        f = _factory(vm, owner)
        addrs = [_create(f, vm, alice, title=f"T{i}") for i in range(5)]
        page = f.get_monocles_page(1, 2)
        assert page == {"total": 5, "offset": 1, "addresses": addrs[1:3]}
        assert f.get_monocles_page(4, 10)["addresses"] == addrs[4:]
        assert f.get_monocles_page(5, 10)["addresses"] == []
        assert f.get_monocles_page(-1, 10)["addresses"] == []
        assert f.get_monocles_page(0, 0)["addresses"] == []
        assert len(f.get_monocles_page(0, 1000)["addresses"]) == 5


def test_filters_by_creator_and_type():
    vm = VMContext()
    owner, alice, bob = create_test_addresses(3)
    with vm.activate():
        f = _factory(vm, owner)
        a = _create(f, vm, alice, itype="market")
        b = _create(f, vm, bob, itype="filing")
        c = _create(f, vm, alice, itype="filing")
        assert f.get_monocles_by_creator(to_hex(alice)) == [a, c]
        assert f.get_monocles_by_creator(to_hex(bob)) == [b]
        assert f.get_monocles_by_type("filing") == [b, c]
        assert f.get_monocles_by_type("nope") == []


def test_unknown_monocle_meta_reverts():
    vm = VMContext()
    owner, = create_test_addresses(1)
    with vm.activate():
        f = _factory(vm, owner)
        with vm.expect_revert("Unknown Monocle"):
            f.get_monocle_meta("0x" + "11" * 20)
        assert f.is_registered("0x" + "11" * 20) is False


def test_withdraw_fees_only_owner_rejects_empty_and_pays_exact_sum():
    vm = VMContext()
    owner, alice, bob = create_test_addresses(3)
    with vm.activate():
        rec = install_recorder(vm)
        f = _factory(vm, owner, creation_stake=7)
        vm.sender = owner
        with vm.expect_revert("No fees to withdraw"):
            f.withdraw_fees()
        _create(f, vm, alice, value=7)
        _create(f, vm, bob, value=9)
        vm.sender = alice
        with vm.expect_revert("Only the factory owner"):
            f.withdraw_fees()
        vm.sender = owner
        assert f.withdraw_fees() == "14"
        assert f.get_collected_fees() == "0"
        assert rec.transfers_to(owner) == 14
        with vm.expect_revert("No fees to withdraw"):
            f.withdraw_fees()


def test_receive_residual_is_withdrawable_and_rejects_zero():
    vm = VMContext()
    owner, monocle_like = create_test_addresses(2)
    with vm.activate():
        rec = install_recorder(vm)
        f = _factory(vm, owner)
        vm.sender = monocle_like
        with vm.expect_revert("positive value"):
            f.receive_residual()
        vm.value = 9
        f.receive_residual()
        vm.value = 0
        assert f.get_residual_fees() == "9"
        vm.sender = owner
        assert f.withdraw_fees() == "9"
        assert f.get_residual_fees() == "0"
        assert rec.transfers_to(owner) == 9


def test_set_creation_stake_owner_only_and_not_retroactive():
    vm = VMContext()
    owner, alice = create_test_addresses(2)
    with vm.activate():
        f = _factory(vm, owner, creation_stake=5)
        a = _create(f, vm, alice, value=5)
        vm.sender = alice
        with vm.expect_revert("Only the factory owner"):
            f.set_creation_stake(1)
        vm.sender = owner
        with vm.expect_revert("non-negative"):
            f.set_creation_stake(-1)
        f.set_creation_stake(50)
        assert f.get_creation_stake() == "50"
        assert f.get_monocle_meta(a)["stake_required"] == "5"
        with vm.expect_revert("stake too low"):
            _create(f, vm, alice, value=5)


def test_factory_has_no_pause_or_admin_escape_hatches():
    vm = VMContext()
    owner, = create_test_addresses(1)
    with vm.activate():
        f = _factory(vm, owner)
        for name in ("pause", "unpause", "set_owner", "close_monocle", "adjudicate", "sweep", "set_monocle_code"):
            assert not hasattr(f, name), name
