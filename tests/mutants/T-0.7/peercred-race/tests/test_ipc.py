from daimon.common import ipc


def test_mutante_ist_im_normalfall_funktionsfaehig():
    assert callable(ipc.create_listener)
    assert callable(ipc.authorize_peer)
