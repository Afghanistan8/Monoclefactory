from monocle_sdk import STUDIO_NEXT_CHAIN_ID, STUDIO_NEXT_RPC, describe_transaction_outcome
from monocle_sdk.client import NETWORKS


def _tx(state, result, outcome="accepted"):
    return {"lifecycle": {"state": state, "outcome": outcome}, "tx_execution_result_name": result}


def test_finalized_return_is_success():
    assert describe_transaction_outcome(_tx("finalized", "FINISHED_WITH_RETURN")).succeeded


def test_reverted_is_failure_even_when_accepted_or_finalized():
    assert not describe_transaction_outcome(_tx("decided", "FINISHED_WITH_ERROR"), require_finalized=False).succeeded
    assert not describe_transaction_outcome(_tx("finalized", "FINISHED_WITH_ERROR")).succeeded


def test_decided_is_not_enough_when_finality_required():
    tx = _tx("decided", "FINISHED_WITH_RETURN")
    assert not describe_transaction_outcome(tx).succeeded
    assert describe_transaction_outcome(tx, require_finalized=False).succeeded


def test_missing_result_is_never_success():
    assert not describe_transaction_outcome({"lifecycle": {"state": "finalized"}}).succeeded


def test_studio_next_preset():
    chain = NETWORKS["studio-next"]
    assert chain.id == STUDIO_NEXT_CHAIN_ID == 61997
    assert chain.rpc_urls["default"]["http"][0] == STUDIO_NEXT_RPC
