"""Configurable challenge window: set per deployment on the factory, passed to
every Monocle, bounded, and never a way around bonds or fail-closed rules."""

from gltest.direct import VMContext, create_test_addresses

from conftest import (
    FACTORY_PATH,
    MONOCLE_PATH,
    advance,
    claims,
    decide,
    deploy,
    deploy_monocle,
    install_recorder,
    submit,
)


def test_default_window_is_one_hour():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator)
        info = m.get_monocle_info()
        assert info["challenge_window_seconds"] == "3600"
        assert info["constants"]["challenge_window_seconds"] == 3600


def test_custom_window_sets_deadline_and_allows_finalize_after_it():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator, challenge_window=180)
        assert m.get_monocle_info()["challenge_window_seconds"] == "180"
        a = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        decide(m, vm, creator, a)
        r = m.get_round_info("1")
        assert int(r["challenge_deadline"]) == int(r["decided_at"]) + 180
        advance(vm, 180)
        with vm.expect_revert("Challenge window is still open"):
            m.finalize("1")
        advance(vm, 1)
        m.finalize("1")
        assert m.get_live_interpretation()["finality"] == "final"


def test_short_window_does_not_bypass_bonds_or_fail_closed():
    vm = VMContext()
    creator, alice = create_test_addresses(2)
    with vm.activate():
        m = deploy_monocle(vm, creator, challenge_window=60)
        with vm.expect_revert("bond too low"):
            submit(m, vm, alice, "Rising.", claims("It rose"), 1)
        a = submit(m, vm, alice, "Rising.", claims("It rose"), 10)
        decide(m, vm, creator, a, confidence="0.3")
        assert m.get_round_info("1")["status"] == "inconclusive"
        assert m.get_monocle_info()["live_interpretation_id"] == ""


def test_window_bounds_are_enforced_on_monocle():
    vm = VMContext()
    creator, = create_test_addresses(1)
    with vm.activate():
        for bad in (0, 59, 7 * 86400 + 1):
            with vm.expect_revert("challenge_window_seconds must be between"):
                deploy_monocle(vm, creator, challenge_window=bad)
        deploy_monocle(vm, creator, challenge_window=60)
        deploy_monocle(vm, creator, challenge_window=7 * 86400)


def test_factory_passes_its_window_to_every_monocle():
    vm = VMContext()
    owner, alice = create_test_addresses(2)
    with vm.activate():
        rec = install_recorder(vm)
        vm.sender = owner
        f = deploy(vm, FACTORY_PATH, MONOCLE_PATH.read_text(encoding="utf-8"), "", 0, 10, 5, 20, 180)
        assert f.get_bond_config()["challenge_window_seconds"] == "180"
        rec.clear()
        vm.sender = alice
        addr = f.create_monocle(["https://example.com/a", "https://example.org/b"], "market", "T", "D", "")
        (dep,) = rec.deploys
        assert dep["calldata"]["args"][8] == 180
        assert f.get_monocle_meta(addr)["challenge_window_seconds"] == "180"


def test_factory_rejects_out_of_bounds_window():
    vm = VMContext()
    owner, = create_test_addresses(1)
    with vm.activate():
        vm.sender = owner
        for bad in (0, 30, 8 * 86400):
            with vm.expect_revert("challenge_window_seconds must be between"):
                deploy(vm, FACTORY_PATH, "code", "", 0, 10, 5, 20, bad)
