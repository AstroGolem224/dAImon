from daimon.common import ipc


def test_vertrag_ist_vorhanden():
    assert callable(ipc.listen)
    assert callable(ipc.accept)
    assert callable(ipc.pruefe_typ)
    assert callable(ipc.peer_of)
