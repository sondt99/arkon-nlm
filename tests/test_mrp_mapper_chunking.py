from app.ai.mrp.mapper import CHUNK_TARGET_CHARS, build_chunks


def test_large_outline_section_is_hard_split_to_map_budget():
    full_text = "A" * (CHUNK_TARGET_CHARS * 2 + 123)
    outline = [{
        "title": "Large security playbook",
        "level": 1,
        "char_start": 0,
        "char_end": len(full_text),
        "children": [],
    }]

    chunks = build_chunks(full_text, outline, "standard")

    assert len(chunks) == 3
    assert all(chunk.end_char - chunk.start_char <= CHUNK_TARGET_CHARS for chunk in chunks)
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == len(full_text)
