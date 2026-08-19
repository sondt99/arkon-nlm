"""SSRF hop validation and MinIO prefix-move safety."""

import pytest

from app.services.kb_service import _validate_url_not_internal


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://0.0.0.0/x",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/x",                          # private
        "http://192.168.1.1/x",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",                # IPv4-mapped loopback
        "http://224.0.0.1/x",                          # multicast
        "ftp://example.com/x",                         # wrong scheme
    ],
)
def test_internal_and_malformed_targets_are_rejected(url):
    with pytest.raises(ValueError):
        _validate_url_not_internal(url)


def test_public_target_is_allowed():
    # 8.8.8.8 is a literal so no DNS is required and the test stays hermetic.
    _validate_url_not_internal("https://8.8.8.8/health")


@pytest.mark.asyncio
async def test_redirect_to_internal_target_is_blocked(monkeypatch):
    """The core of the SSRF fix: hop 2 must be validated, not just hop 1.

    With follow_redirects=True only the submitted URL was checked, so an
    attacker-controlled host could 302 to the metadata service and the body was stored in
    Source.full_text — readable SSRF rather than blind.
    """
    from app.services import kb_service

    class _Resp:
        def __init__(self, status, location=None, text=""):
            self.status_code = status
            self.headers = {"location": location} if location else {}
            self.text = text

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            # First hop is the public host; it redirects inward.
            if "8.8.8.8" in url:
                return _Resp(302, location="http://169.254.169.254/latest/meta-data/")
            return _Resp(200, text="SECRET-CREDENTIALS")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    with pytest.raises(ValueError, match="private/internal"):
        await kb_service._fetch_url_guarded("https://8.8.8.8/start")


@pytest.mark.asyncio
async def test_redirect_chain_is_bounded(monkeypatch):
    from app.services import kb_service

    class _Resp:
        def __init__(self):
            self.status_code = 302
            self.headers = {"location": "https://8.8.8.8/loop"}
            self.text = ""

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    with pytest.raises(ValueError, match="Too many redirects"):
        await kb_service._fetch_url_guarded("https://8.8.8.8/start")


# --------------------------------------------------------------------------- #
# move_prefix must not delete a source it failed to copy
# --------------------------------------------------------------------------- #

def test_is_single_object_reraises_non_missing_errors():
    """A transient MinIO error must not be read as 'this is a folder'.

    That misreading is what let move_prefix delete a file copy_prefix had not copied:
    stat_object failed transiently inside copy_prefix, so it listed `<file>/`, found zero
    objects, logged success, and returned — then move_prefix deleted the original.
    """
    from minio.error import S3Error

    from app.services.storage_service import StorageService

    svc = StorageService.__new__(StorageService)  # no MinIO connection needed

    class _Client:
        def stat_object(self, bucket, key):
            raise S3Error(
                code="InternalError",
                message="transient",
                resource=key,
                request_id="r",
                host_id="h",
                response=None,
            )

    object.__setattr__(svc, "_client", _Client())

    with pytest.raises(S3Error):
        svc._is_single_object("skill-contributions/x/skill/SKILL.md")


def test_is_single_object_returns_false_only_for_missing_key():
    from minio.error import S3Error

    from app.services.storage_service import StorageService

    svc = StorageService.__new__(StorageService)

    class _Client:
        def stat_object(self, bucket, key):
            raise S3Error(
                code="NoSuchKey",
                message="missing",
                resource=key,
                request_id="r",
                host_id="h",
                response=None,
            )

    object.__setattr__(svc, "_client", _Client())
    assert svc._is_single_object("some/folder") is False
