"""Cookie-export conversion (#136 / #87 item 7).

This was 139 lines into the `import_cookies` route handler, so it could not be exercised
without a request and had zero tests. It is worth isolating because it is FORMAT-SENSITIVE
and silently lossy: every field below is a browser-extension detail that can change without
notice, and a wrong guess produces a storage_state that looks valid and then fails to
authenticate — indistinguishable, to the admin, from "my Google session expired".

Note the extraction was NOT done for the reason #87 gives. That item says the rationale is
worker reuse of `import_cookies`; `grep -rn import_cookies app/` returns only the route, so
nothing wants to reuse it. The real payoff is that this logic is now testable.
"""

import pytest

from app.services.notebooklm_service import (
    CookieConversionError,
    cookies_to_storage_state,
)

pytest.importorskip("notebooklm.auth", reason="notebooklm-py is an optional dependency")

REQUIRED = ["SID", "__Secure-1PSIDTS"]


def _required(**overrides):
    """The minimum viable export, in Cookie-Editor shape."""
    return [
        {"name": n, "value": f"val-{n}", "domain": ".google.com", **overrides}
        for n in REQUIRED
    ]


def test_a_minimal_cookie_editor_export_converts():
    state = cookies_to_storage_state(_required())
    assert state["origins"] == []
    assert {c["name"] for c in state["cookies"]} == set(REQUIRED)
    for c in state["cookies"]:
        assert c["sameSite"] == "None"
        assert c["path"] == "/"


def test_expirationDate_and_expires_are_both_honoured():
    """Cookie-Editor writes `expirationDate`; rookiepy writes `expires`."""
    ce = cookies_to_storage_state(_required(expirationDate=1893456000))
    rk = cookies_to_storage_state(_required(expires=1893456000))
    assert all(c["expires"] == 1893456000 for c in ce["cookies"])
    assert all(c["expires"] == 1893456000 for c in rk["cookies"])


def test_a_zero_expiry_is_not_mistaken_for_a_missing_one():
    """The bug this guards: `c.get("expirationDate") or c.get("expires")`.

    A session cookie can carry `expirationDate == 0`, which is falsy — so `or` fell through
    to the other key, or to -1, silently changing the cookie's lifetime.
    """
    state = cookies_to_storage_state(_required(expirationDate=0))
    assert all(c["expires"] == 0 for c in state["cookies"]), (
        "a 0 expiry was treated as absent"
    )


def test_a_missing_expiry_becomes_minus_one():
    state = cookies_to_storage_state(_required())
    assert all(c["expires"] == -1 for c in state["cookies"])


def test_httpOnly_and_http_only_are_both_honoured():
    """camelCase from Cookie-Editor, snake_case from rookiepy."""
    ce = cookies_to_storage_state(_required(httpOnly=True))
    rk = cookies_to_storage_state(_required(http_only=True))
    assert all(c["httpOnly"] is True for c in ce["cookies"])
    assert all(c["httpOnly"] is True for c in rk["cookies"])
    plain = cookies_to_storage_state(_required())
    assert all(c["httpOnly"] is False for c in plain["cookies"])


def test_cookies_from_an_unrelated_domain_are_dropped():
    """An export taken with other tabs open carries cookies for those sites too.

    Writing them into storage_state would hand the NotebookLM browser session credentials
    for unrelated services.
    """
    export = _required() + [
        {"name": "tracker", "value": "x", "domain": ".evil.example"},
        {"name": "sess", "value": "y", "domain": "bank.example.com"},
    ]
    state = cookies_to_storage_state(export)
    domains = {c["domain"] for c in state["cookies"]}
    assert domains == {".google.com"}
    assert "tracker" not in {c["name"] for c in state["cookies"]}


@pytest.mark.parametrize("missing_field", ["name", "value", "domain"])
def test_an_incomplete_cookie_is_skipped_not_written_blank(missing_field):
    bad = {"name": "X", "value": "v", "domain": ".google.com"}
    bad[missing_field] = ""
    state = cookies_to_storage_state(_required() + [bad])
    assert all(c["name"] and c["value"] and c["domain"] for c in state["cookies"])


def test_a_missing_required_cookie_names_what_to_re_export():
    """The admin needs to know WHICH cookie to go back for."""
    with pytest.raises(CookieConversionError) as exc:
        cookies_to_storage_state([
            {"name": "SID", "value": "v", "domain": ".google.com"},
        ])
    assert "__Secure-1PSIDTS" in exc.value.missing
    assert "SID" in exc.value.found


def test_an_empty_export_is_a_conversion_error_not_an_empty_state():
    """Writing `{"cookies": []}` would produce a file that looks saved and never works."""
    with pytest.raises(CookieConversionError) as exc:
        cookies_to_storage_state([])
    assert exc.value.missing
    assert exc.value.found == set()
