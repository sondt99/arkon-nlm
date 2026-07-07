import uuid
from types import SimpleNamespace

import pytest

from app.services import chat_service


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarRows(self._rows)

    def scalar_one_or_none(self):
        return None


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Result(self.rows)


class _LLM:
    def __init__(self, answer="Answer"):
        self.answer = answer
        self.prompt = None
        self.system = None
        self.config = SimpleNamespace(model_id="Optimize")

    async def generate(self, prompt, system=None, temperature=0.5, max_tokens=None, top_p=None):
        self.prompt = prompt
        self.system = system
        return self.answer


class _Registry:
    def __init__(self, llm):
        self.llm = llm

    async def get_chatbot_llm(self):
        return self.llm


class _SequenceLLM(_LLM):
    def __init__(self, answers):
        super().__init__()
        self.answers = list(answers)
        self.calls = 0

    async def generate(self, prompt, system=None, temperature=0.5, max_tokens=None, top_p=None):
        self.calls += 1
        self.prompt = prompt
        self.system = system
        return self.answers.pop(0)


def test_knowledge_context_is_bounded_per_page():
    page = SimpleNamespace(title="Long page", content_md="A" * 10_000)
    prompt = chat_service._build_system_prompt([page])
    assert "A" * chat_service.settings.chat_context_chars_per_page in prompt
    assert "A" * (chat_service.settings.chat_context_chars_per_page + 1) not in prompt
    assert "Do not impose an" in prompt
    assert "arbitrary word limit" in prompt


@pytest.mark.asyncio
async def test_current_question_is_not_duplicated_in_history(monkeypatch):
    current_id = uuid.uuid4()
    conversation = SimpleNamespace(id=uuid.uuid4(), scope_type="global", scope_id=None)
    session = _Session([SimpleNamespace(role="assistant", content="Earlier answer")])
    llm = _LLM()

    async def no_rag(**kwargs):
        return []

    monkeypatch.setattr(chat_service, "rag_search", no_rag)
    answer, _ = await chat_service.generate_reply(
        session=session,
        registry=_Registry(llm),
        conversation=conversation,
        question="Unique current question",
        exclude_message_id=current_id,
    )

    assert answer == "Answer"
    assert llm.prompt.count("Unique current question") == 1
    assert "chat_messages.id !=" in str(session.statement)


@pytest.mark.asyncio
async def test_empty_provider_response_is_rejected(monkeypatch):
    conversation = SimpleNamespace(id=uuid.uuid4(), scope_type="global", scope_id=None)
    session = _Session([])

    async def no_rag(**kwargs):
        return []

    monkeypatch.setattr(chat_service, "rag_search", no_rag)
    with pytest.raises(ValueError, match="empty response"):
        await chat_service.generate_reply(
            session=session,
            registry=_Registry(_LLM("")),
            conversation=conversation,
            question="Question",
        )


@pytest.mark.asyncio
async def test_short_substantive_answer_is_expanded(monkeypatch):
    conversation = SimpleNamespace(id=uuid.uuid4(), scope_type="global", scope_id=None)
    session = _Session([])
    llm = _SequenceLLM(["Too short.", "D" * 2_000])

    async def no_rag(**kwargs):
        return []

    monkeypatch.setattr(chat_service, "rag_search", no_rag)
    answer, _ = await chat_service.generate_reply(
        session=session,
        registry=_Registry(llm),
        conversation=conversation,
        question="Explain the complete architecture and every important trade-off.",
    )
    assert len(answer) == 2_000
    assert llm.calls == 2


def test_explicit_brief_request_is_not_forced_to_expand():
    assert not chat_service._should_expand_answer("Tóm tắt ngắn trong 3 gạch đầu dòng", "Short")
    assert chat_service._should_expand_answer("Phân tích kiến trúc chi tiết", "Short")
