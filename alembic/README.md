# Migrations

`entrypoint.sh` runs `alembic upgrade head` unattended on container start. Nothing in the
startup path pauses for an operator, so a destructive migration takes effect before anyone
can look at it. Write every revision on that assumption.

Commands are in `COMMANDS.md` (repo root). Autogenerate hazards are documented in
`env.py` — read the `include_object` comment before running `alembic revision --autogenerate`.

---

## Rule: dropping a populated column requires a data migration

A `drop_column` on a column users have written to destroys that data irreversibly. It is
not enough for the downgrade to re-add the column — an empty column is not a restore.

Before dropping a column, do one of:

1. **Copy it forward** into its replacement with an `INSERT ... SELECT` or `UPDATE`,
   *before* the drop in the same `upgrade()`.
2. **Archive it** explicitly, if nothing consumes it any more (see below).
3. **Establish that there is nothing to lose** — derived data that can be regenerated,
   or a column no deployment ever populated.

Then record which of the three applies in `DROP_DISPOSITIONS` in
`tests/test_migration_backfill.py`. That table is enforced:
`test_every_dropped_column_has_a_recorded_disposition` fails on any column drop in an
`upgrade()` that is not listed, so the decision has to be made and written down rather
than merged by default. This is the durable half of issue #41.

### Make the data step un-abortable

A migration that crashes on a database it was not expecting is a worse outage than the bug
it was fixing, and `alembic upgrade head` is the container's startup path. So:

- Gate a `SELECT` from a column on the column actually existing — `_has_column()` in
  `014_wiki_draft_revision.py` and `018_drop_skill_description.py` is the pattern.
- Use `ALTER TABLE ... DROP COLUMN IF EXISTS` rather than `op.drop_column`, or the
  unguarded drop defeats the guard on the copy.
- Make the copy a clean no-op when there is nothing to copy. `WHERE col ~ '[^[:space:]]'`
  skips rows whose content is NULL or blank, and `HAVING count(*) > 0` on an aggregate
  archive writes no row at all rather than a row holding an empty array.
- Adding a `NOT NULL` column needs a `server_default`, or a nullable-add → backfill →
  `SET NOT NULL` sequence. `NOT NULL` with neither aborts on any table that already has
  rows. 018 shipped that mistake for `skill_contributions.scope_type`, which meant 018
  could not be applied at all to an install that had been running 017 in production.

---

## Archived data

Two columns were dropped without a successor. Their contents are archived as JSON in
`app_config`, which is the only durable store that already exists both in a migrated
database and in a fresh one — a dedicated side table would diverge the two, and because
`env.py`'s `include_object` only exempts unmodelled *indexes*, an unmodelled archive table
would surface in the next autogenerate as a proposed `DROP TABLE`.

| `app_config.key` | Written by | Holds |
|---|---|---|
| `archived_018_skills_description` | 018 | `[{id, slug, name, description}]` for every skill that had a description |
| `archived_018_skill_contributions_description` | 018 | `[{id, skill_id, contributor_id, title, description}]` |

The rows are inert: `ConfigService` reads `app_config` only by explicit key, and both
`get_all()` and `update_many()` iterate the fixed `ALL_CONFIG_KEYS` allowlist, so an
archive key can never appear in — or be overwritten by — the admin config UI.

To read one back:

```sql
SELECT jsonb_pretty(value::jsonb) FROM app_config
WHERE key = 'archived_018_skills_description';
```

Restoring means re-adding the column by hand and updating by primary key. There is no
migration that does it, because nothing in the application reads either column any more.

---

## What the 014 and 018 backfills do not do

Alembic never re-runs an applied revision. A database whose `alembic_version` is already
past 014 or 018 has no archive and no way to produce one — the source columns are gone and
the rows they held were destroyed by the original revisions. The recovery path there is a
restore from a pre-014 / pre-018 backup.

The backfills protect the cases still ahead of those revisions: an environment sitting at
or below 013 / 017, and fresh restores of old dumps.
