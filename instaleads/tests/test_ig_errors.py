from backend.instagram.ig_errors import classify_exception


def test_classify_timeout():
    desc = classify_exception(Exception("network timeout while requesting profile"))
    assert desc.code == "NETWORK_TIMEOUT"
    assert desc.retryable is True


def test_classify_challenge():
    desc = classify_exception(Exception("checkpoint challenge required"))
    assert desc.code == "IG_CHALLENGE"
    assert desc.action == "rotate_identity"


def test_classify_unclassified():
    desc = classify_exception(Exception("unexpected thing happened"))
    assert desc.code == "UNCLASSIFIED"
