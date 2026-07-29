from daimon.common import ipc


def test_mutante_ist_importierbar():
    assert callable(ipc.authorize_peer)
