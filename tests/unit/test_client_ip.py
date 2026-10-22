"""IP del cliente para los límites por IP, detrás de Vercel y del router de Heroku."""

from app.core.deps import forwarded_ip

PEER = "10.1.2.3"  # router de Heroku


def test_without_trusted_proxies_the_header_is_ignored() -> None:
    assert forwarded_ip(["1.1.1.1"], PEER, 0) == PEER


def test_heroku_only_uses_the_ip_the_router_appended() -> None:
    assert forwarded_ip(["9.9.9.9, 1.1.1.1"], PEER, 1) == "1.1.1.1"


def test_vercel_and_heroku_use_the_ip_vercel_saw() -> None:
    assert forwarded_ip(["1.1.1.1, 76.76.21.9"], PEER, 2) == "1.1.1.1"


def test_a_spoofed_entry_before_the_proxies_is_ignored() -> None:
    assert forwarded_ip(["6.6.6.6, 1.1.1.1, 76.76.21.9"], PEER, 2) == "1.1.1.1"


def test_a_direct_call_with_fewer_entries_uses_the_oldest() -> None:
    # El reloj llama directo a Heroku: solo está la entrada del router.
    assert forwarded_ip(["1.1.1.1"], PEER, 2) == "1.1.1.1"


def test_repeated_headers_and_blanks_are_joined() -> None:
    assert forwarded_ip(["1.1.1.1", " , 76.76.21.9"], PEER, 2) == "1.1.1.1"


def test_no_header_falls_back_to_the_connection() -> None:
    assert forwarded_ip([], PEER, 2) == PEER
