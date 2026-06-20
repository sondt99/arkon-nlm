import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai.mrp import writer
from app.ai.mrp.writer import run_refine_phase


class FakeLLM:
    config = SimpleNamespace(model_id="test-model")

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self.fail_first = False

    async def generate(self, prompt, **_kwargs):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise TimeoutError("temporary gateway timeout")
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return "# Updated\n\nPreserved existing knowledge and added evidence."


class FakeTracker:
    async def update(self, *_args, **_kwargs):
        return None


def test_parallel_refine_uses_one_preloaded_wiki_snapshot():
    async def scenario():
        pages = [
            SimpleNamespace(
                slug=f"concept/page-{index}",
                title=f"Page {index}",
                content_md=f"Existing content {index}",
            )
            for index in range(4)
        ]
        plan = SimpleNamespace(plan_json={
            "pages": [
                {
                    "action": "UPDATE",
                    "slug": page.slug,
                    "title": page.title,
                    "page_type": "concept",
                    "entity_names": [],
                    "priority": 1,
                }
                for page in pages
            ],
            "_claims": [],
        })
        source = SimpleNamespace(
            id="source-id", scope_type="global", scope_id=None,
        )
        llm = FakeLLM()
        list_pages = AsyncMock(return_value=pages)

        with patch("app.services.wiki_service.list_pages", list_pages):
            results = await run_refine_phase(
                session=object(),
                source=source,
                plan=plan,
                chunk_extracts=[],
                full_text="Source text",
                llm=llm,
                embedding_provider=None,
                kt_slug=None,
                tracker=FakeTracker(),
            )

        assert len(results) == 4
        assert llm.max_active > 1
        list_pages.assert_awaited_once()

    asyncio.run(scenario())


def test_parallel_refine_retries_transient_writer_failure_without_stub():
    async def scenario():
        plan = SimpleNamespace(plan_json={
            "pages": [{
                "action": "CREATE",
                "slug": "concept/retry",
                "title": "Retry",
                "page_type": "concept",
                "entity_names": [],
                "priority": 1,
            }],
            "_claims": [],
        })
        source = SimpleNamespace(id="source-id", scope_type="global", scope_id=None)
        llm = FakeLLM()
        llm.fail_first = True

        with (
            patch("app.services.wiki_service.list_pages", AsyncMock(return_value=[])),
            patch.object(writer, "WRITER_RETRY_DELAYS", (0, 0)),
        ):
            results = await run_refine_phase(
                session=object(), source=source, plan=plan, chunk_extracts=[],
                full_text="Source text", llm=llm, embedding_provider=None,
                kt_slug=None, tracker=FakeTracker(),
            )

        assert llm.calls == 2
        assert len(results) == 1
        assert "Page generation failed" not in results[0].content_md

    asyncio.run(scenario())
