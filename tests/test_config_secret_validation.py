"""`Settings.validate_secrets` is unreachable from the rest of the suite.

`tests/conftest.py` sets `ARKON_ALLOW_DEFAULT_SECRET=1` process-wide, and the validator
returns at its first line when that flag is set. Every weak-credential guard is therefore
dead for all 937 other tests — deleting the whole method body left them green, which is how
`weak_minio_access` came to be missing the placeholder `.env.docker.example` actually ships
while its `weak_minio_secret` counterpart was present.

These tests construct `Settings` directly with the flag off, so the guards are exercised for
real. `_strong()` supplies a fully valid credential set; each test flips exactly one field to
a known-bad value and asserts the boot refuses it.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings

# A credential set that must pass cleanly. Every test below starts from this and
# degrades one field, so an assertion failure points at that field and nothing else.
_STRONG = {
    "arkon_allow_default_secret": False,
    "secret_key": "z9Qv3rXk7Lm2Pw8Nt5Hb1Yc4Jd6Fg0Ss",
    "default_admin_password": "Str0ng!Admin#Passw0rd",
    "minio_access_key": "arkon-prod-access",
    "minio_secret_key": "arkon-prod-secret-value",
    "redis_password": "arkon-prod-redis-pw",
    "database_url": "postgresql+asyncpg://arkon:realpassword@postgres:5432/arkon",
}


def _strong(**overrides):
    return {**_STRONG, **overrides}


def test_a_fully_configured_settings_object_validates():
    """Guards the fixture itself: if this fails the other tests prove nothing."""
    assert Settings(**_strong()).minio_access_key == "arkon-prod-access"


@pytest.mark.parametrize(
    "field, value",
    [
        # The exact strings .env.docker.example / .env.local.example ship. Each one
        # reaching a deployment unchanged is the scenario these guards exist for.
        ("secret_key", "change-me-to-a-random-secret-string"),
        ("default_admin_password", "change-me-admin-password"),
        ("default_admin_password", "admin123"),
        ("minio_access_key", "change-me-minio-access-key"),
        ("minio_secret_key", "change-me-minio-secret-key"),
        ("redis_password", "change-me-redis-password"),
        # Upstream image defaults, which are what you get by omitting the key entirely.
        ("minio_access_key", "minioadmin"),
        ("minio_secret_key", "minioadmin123"),
        ("redis_password", ""),
    ],
)
def test_known_placeholder_credentials_are_refused_at_boot(field, value):
    with pytest.raises(ValidationError) as exc:
        Settings(**_strong(**{field: value}))
    # The message must name the offending variable — an operator reads only this line.
    assert field.upper() in str(exc.value)


def test_both_minio_credentials_are_checked_independently():
    """The regression this file was added for.

    `weak_minio_access` omitted `change-me-minio-access-key` while `weak_minio_secret`
    carried its counterpart, so a deployment that fixed only the value the app complained
    about still shipped the placeholder as MINIO_ROOT_USER — while
    `.env.docker.example` claimed "Both MinIO credentials are rejected at startup".
    """
    for field in ("minio_access_key", "minio_secret_key"):
        placeholder = f"change-me-{field.replace('_', '-').replace('minio-', 'minio-')}"
        with pytest.raises(ValidationError):
            Settings(**_strong(**{field: placeholder}))


def test_the_escape_hatch_waives_every_credential_check_not_just_the_secret_key():
    """Pins current behaviour, which is broader than the flag's name suggests.

    `ARKON_ALLOW_DEFAULT_SECRET` short-circuits the whole validator, so it also waives the
    admin password, both MinIO credentials, Redis and the DATABASE_URL check. That is a
    sharp edge worth failing loudly on if it ever changes shape: a developer who sets this
    to unblock a local JWT secret is also silently permitted to ship `minioadmin`.
    """
    everything_weak = {
        "arkon_allow_default_secret": True,
        "secret_key": "change-me-to-a-random-secret-string",
        "default_admin_password": "admin123",
        "minio_access_key": "minioadmin",
        "minio_secret_key": "minioadmin123",
        "redis_password": "",
    }
    assert Settings(**everything_weak).minio_access_key == "minioadmin"
