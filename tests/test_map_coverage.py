"""MAP-phase coverage floor and worker idempotency.

These are the first tests to touch app/worker.py or the MAP coverage rule at all — both
had zero references before (issue #61).
"""

import inspect

from app.ai.mrp.mapper import MIN_MAP_COVERAGE, MapCoverageError, run_map_phase


def test_coverage_floor_is_a_real_threshold():
    """Not 0 (accept anything) and not 1.0 (reject any single bad chunk)."""
    assert 0.0 < MIN_MAP_COVERAGE < 1.0
    assert MIN_MAP_COVERAGE >= 0.5, "a floor below half the document is not a floor"


def test_map_phase_raises_rather_than_returning_a_partial_document():
    """The rule must be an exception, not a logged warning.

    The failure this guards against is not a crash — it is a *silent success*:
    run_commit_phase set status="ready", progress=100, and explicitly cleared
    error_message, so a page synthesised from four of forty chunks looked identical to a
    complete ingest. Only raising reaches the worker's error handler, which marks the
    source `error` with the detail.
    """
    src = inspect.getsource(run_map_phase)
    assert "MapCoverageError" in src, (
        "run_map_phase no longer raises on low coverage — a partial document would be "
        "committed as complete again"
    )
    assert "MIN_MAP_COVERAGE" in src
    assert issubclass(MapCoverageError, Exception)


def test_worker_marks_source_error_on_any_base_exception():
    """The MRP task must catch BaseException, not Exception.

    arq delivers CancelledError on job timeout. Catching only Exception left sources stuck
    mid-pipeline with no error recorded, which is the same class of bug as the artifact
    stuck at "processing".
    """
    import app.worker as worker

    src = inspect.getsource(worker.ingest_map_reduce_task)
    assert "except BaseException" in src
    assert 'status = "error"' in src


def test_image_persistence_is_idempotent():
    """A retry must be able to succeed.

    SourceImage carries UniqueConstraint(source_id, image_index) and max_tries=3, so
    re-inserting image_index=0 raised IntegrityError on every retry — leaving the source
    permanently unprocessable, recoverable only by deleting rows by hand.
    """
    import app.worker as worker

    src = inspect.getsource(worker.ingest_file_task)
    assert "sql_delete(SourceImage)" in src, (
        "ingest_file_task no longer clears prior SourceImage rows, so a retry will hit the "
        "unique constraint and fail permanently"
    )


def test_generate_task_poll_budget_is_below_the_job_timeout():
    """Equal budgets meant arq always won the race and the artifact stuck at 'processing'."""
    import app.worker as worker

    src = inspect.getsource(worker.notebooklm_generate_task)
    assert "poll_budget" in src
    assert "worker_job_timeout" in src
    assert "except BaseException" in src, (
        "CancelledError from an arq timeout must be caught, or status stays 'processing'"
    )


def test_skill_task_reraises_so_arq_records_a_failure():
    """Swallowing meant arq marked the job COMPLETE and max_tries never engaged."""
    import app.worker as worker

    src = inspect.getsource(worker.ingest_skill_task)

    # Locate the skill error handler specifically. Splitting on "except Exception as e:"
    # and taking the last block picks up the temp-file cleanup handler instead, which is a
    # different concern — that mistake is why this assertion is anchored on the status
    # write rather than on the except line.
    marker = 'skill.status = "error"'
    assert marker in src
    handler = src[src.index(marker):]

    assert "raise" in handler, "ingest_skill_task swallows failures again"
    assert "delete_prefix" in handler, "partial upload is no longer cleaned up"


def test_notebook_ingest_eager_loads_source():
    """notebook.source was default-lazy, so touching it raised MissingGreenlet.

    Because notebook.source_id is non-NULL in the normal case, this meant ingesting a
    NotebookLM artifact back into the wiki never worked at all.
    """
    import app.worker as worker

    src = inspect.getsource(worker.notebooklm_ingest_artifact_task)
    assert "selectinload" in src, (
        "the notebook fetch no longer eager-loads .source — MissingGreenlet returns"
    )
