"""Store MCP tokens as SHA-256 digests, and give them an expiry.

Tokens were stored in plaintext in an indexed employees column and matched by SQL
equality. Any read of that table — a pg_dump, a backup, a replica, a support query, an
over-broad analytics grant — yielded directly usable credentials for every employee's full
document scope. There was no expiry column at all, so a token exfiltrated from a
developer's Claude Desktop config years earlier still worked.

Existing tokens are migrated rather than revoked: the digest is computed in SQL from the
stored plaintext, so every currently-configured Claude Desktop keeps working and the
plaintext column is then dropped. pgcrypto is not required — Postgres' built-in
`sha256(bytea)` (available since 11) is used, which matches
hashlib.sha256(token.encode()).hexdigest() in the application.

Tokens that already exist get an expiry counted from now, so nobody is locked out at
migration time by a backdated timestamp.

Revision ID: 031
Revises: 030
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_EXPIRY_DAYS = 90


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column(
            "mcp_token_hash",
            sa.String(length=64),
            nullable=True,
            comment=(
                "SHA-256 hex digest of the MCP bearer token. The token itself is shown "
                "once at generation and never stored."
            ),
        ),
    )
    op.add_column(
        "employees",
        sa.Column(
            "mcp_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Hard expiry for the MCP token; enforced in MCPAuthService.verify_token.",
        ),
    )

    # Migrate in place so existing integrations keep working. encode(sha256(...), 'hex')
    # produces the same lowercase hex digest as hashlib in the app.
    op.execute(
        f"""
        UPDATE employees
        SET mcp_token_hash = encode(sha256(mcp_token::bytea), 'hex'),
            mcp_token_expires_at = now() + interval '{_DEFAULT_EXPIRY_DAYS} days'
        WHERE mcp_token IS NOT NULL
        """
    )

    op.create_index("ix_employees_mcp_token_hash", "employees", ["mcp_token_hash"])
    op.create_unique_constraint(
        "uq_employees_mcp_token_hash", "employees", ["mcp_token_hash"]
    )

    # Drop the plaintext last, after the digests are populated and indexed.
    op.drop_index("ix_employees_mcp_token", table_name="employees")
    op.drop_column("employees", "mcp_token")


def downgrade() -> None:
    """Reversible in shape, but NOT in data — digests cannot be inverted.

    The column comes back and the schema matches the pre-031 state, but every token is
    left NULL: users must regenerate. That is stated here rather than pretended away,
    because a downgrade that silently logged everyone out of MCP without saying so is
    worse than one that says it will.
    """
    op.add_column(
        "employees",
        sa.Column(
            "mcp_token",
            sa.String(length=500),
            nullable=True,
            comment="Bearer token for MCP authentication",
        ),
    )
    op.create_index("ix_employees_mcp_token", "employees", ["mcp_token"])
    op.create_unique_constraint("employees_mcp_token_key", "employees", ["mcp_token"])

    op.drop_constraint("uq_employees_mcp_token_hash", "employees", type_="unique")
    op.drop_index("ix_employees_mcp_token_hash", table_name="employees")
    op.drop_column("employees", "mcp_token_expires_at")
    op.drop_column("employees", "mcp_token_hash")
