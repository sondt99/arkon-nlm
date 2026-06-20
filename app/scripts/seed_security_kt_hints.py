"""
Seed default extraction_hints for well-known security knowledge types.

Idempotent: only sets hints for KTs whose slug matches a known security pattern
AND whose extraction_hints field is currently NULL (never overwrites admin edits).

Run after alembic migrate 024:
    python -m app.scripts.seed_security_kt_hints
Or called from main.py lifespan after migrations.
"""

import asyncio

from loguru import logger
from sqlalchemy import select

from app.database import async_session_factory
from app.database.models import KnowledgeType

# ---------------------------------------------------------------------------
# Default hints per slug pattern (matched as substring or exact)
# ---------------------------------------------------------------------------

_PENTEST_HINTS = """\
These documents contain offensive security knowledge — penetration testing, red team operations, \
exploit development, and attack technique documentation.

**Preservation rules (override general keep/drop rules):**
- KEEP all platform/version-specific commands and syntax verbatim. Do NOT generalize.
  Example: "EXEC master..xp_cmdshell 'whoami'" must be preserved as-is, NOT condensed to "stored procedure execution".
- KEEP target-specific bypass techniques as separate concept pages per platform.
  Example: SQLi bypass on MySQL, MSSQL, Oracle, PostgreSQL each deserve their own concept page.
- KEEP exact tool commands with flags and options (e.g. "sqlmap -u URL --dbs --batch --level=5").
- KEEP CVE identifiers, CWE numbers, CVSS scores — these are primary keys, not metadata.
- KEEP version-specific behaviors (e.g. "works on Apache 2.4.49 only, patched in 2.4.50").
- KEEP exact payload strings, shellcode snippets, and proof-of-concept code.
- KEEP step-by-step exploitation sequences in order, with the exact commands used at each step.
- KEEP WAF/AV bypass specifics (e.g. "Sophos blocks this pattern, use chunked encoding instead").
- KEEP port numbers, protocol details, service banner strings that identify targets.
- KEEP lateral movement paths, privilege escalation vectors with exact commands.

**Entity types for this domain:**
- `technique` — an attack method, bypass, or exploitation procedure (e.g. "SQL injection via stored procedure")
- `tool` — an offensive security tool (e.g. "sqlmap", "Metasploit", "Cobalt Strike")
- `cve` — a specific CVE identifier and its affected versions
- `payload` — a specific exploit payload, shellcode, or proof-of-concept

**Page structure for technique pages:**
- Slug: concept/<technique-name>-<platform> (e.g. concept/sqli-stored-proc-mssql)
- Each distinct platform variant = its own concept page
- Include: prerequisites, exact steps, example commands, detection/defense notes
- Link technique pages to the tools and CVEs they relate to
"""

_REDTEAM_HINTS = """\
These documents describe red team tactics, techniques, and procedures (TTPs) following \
frameworks like MITRE ATT&CK, Cyber Kill Chain, or custom playbooks.

**Preservation rules (override general keep/drop rules):**
- KEEP all MITRE ATT&CK technique IDs (e.g. T1059.001, T1003.001) — these are canonical identifiers.
- KEEP specific tool invocations, living-off-the-land binary (LOLBin) commands exactly as written.
- KEEP C2 framework specifics (e.g. "Cobalt Strike beacon with sleep jitter 30-90s").
- KEEP evasion technique specifics tied to particular EDR/AV vendors.
- KEEP operational security (OPSEC) considerations with the exact constraints described.
- KEEP infrastructure details: redirector configs, domain fronting setups, malleable C2 profiles.
- KEEP TTPs specific to target environments (Active Directory, cloud tenants, OT/ICS).
- KEEP persistence mechanisms with the exact registry keys, scheduled task names, or WMI filters.

**Entity types for this domain:**
- `technique` — a TTP with MITRE ID if available
- `tool` — C2 frameworks, post-exploitation tools, custom implants
- `cve` — CVE or 0-day being exploited
- `payload` — specific implant, dropper, or exploit payload

**Page slug convention:**
- concept/ttp-<mitre-id> for MITRE-mapped techniques (e.g. concept/ttp-t1059-001)
- concept/<descriptive-name> for non-MITRE TTPs
"""

_VULN_RESEARCH_HINTS = """\
These documents cover vulnerability research, exploit development, and bug bounty findings.

**Preservation rules (override general keep/drop rules):**
- KEEP all affected version ranges exactly as stated (e.g. "< 3.4.2, >= 3.0.0").
- KEEP root cause analysis details — the exact code path, race condition timing, or memory layout.
- KEEP PoC code and exploitation primitives verbatim.
- KEEP patch diffs and the specific function/line that was changed.
- KEEP CVSS vector strings (e.g. CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H).
- KEEP bounty/disclosure timelines — these establish precedent for similar findings.
- KEEP environment-specific conditions required for exploitation (e.g. "requires PHP 8.0 with opcache enabled").

**Entity types for this domain:**
- `cve` — CVE or internal bug tracker ID
- `technique` — the vulnerability class and exploitation method
- `tool` — fuzzing tools, debuggers, exploit frameworks used in research
"""

# Map: if KT slug contains any of these substrings → apply hints
_SLUG_HINTS_MAP: list[tuple[list[str], str]] = [
    (["pentest", "penetration", "offensive", "exploit", "sqli", "injection", "bypass"], _PENTEST_HINTS),
    (["redteam", "red-team", "ttp", "att&ck", "attck", "c2", "implant"], _REDTEAM_HINTS),
    (["vuln", "vulnerability", "cve", "bugbounty", "bug-bounty", "0day", "zeroday"], _VULN_RESEARCH_HINTS),
]


def _match_hints(slug: str) -> str | None:
    slug_lower = slug.lower()
    for patterns, hints in _SLUG_HINTS_MAP:
        if any(p in slug_lower for p in patterns):
            return hints
    return None


async def seed_security_kt_hints() -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(KnowledgeType).where(KnowledgeType.extraction_hints.is_(None))
        )
        kts = result.scalars().all()

        updated = 0
        for kt in kts:
            hints = _match_hints(kt.slug)
            if hints is None:
                # Also try matching against the name
                hints = _match_hints(kt.name.lower().replace(" ", "-"))
            if hints:
                kt.extraction_hints = hints
                updated += 1
                logger.info(f"Seeded extraction_hints for KT slug='{kt.slug}'")

        if updated:
            await session.commit()
            logger.success(f"Seeded extraction_hints for {updated} security knowledge type(s)")
        else:
            logger.info("No security KTs without hints found — nothing to seed")


if __name__ == "__main__":
    asyncio.run(seed_security_kt_hints())
