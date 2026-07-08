from app.agent.grounding import validate_answer
from app.agent.provider import AnswerDraft, GroundedFinding

EVIDENCE = [{"id": "E1", "fact": '{"region": "East", "total": 120.5}'}]


def test_grounding_accepts_supported_numbers() -> None:
    draft = AnswerDraft(
        summary="East leads.",
        findings=[GroundedFinding(text="East total is 120.5.", evidence_ids=["E1"])],
    )

    assert validate_answer(draft, EVIDENCE)


def test_grounding_rejects_unsupported_numbers_and_evidence() -> None:
    invented = AnswerDraft(
        summary="Invented.",
        findings=[GroundedFinding(text="East total is 999.", evidence_ids=["E1"])],
    )
    unknown = AnswerDraft(
        summary="Unknown.",
        findings=[GroundedFinding(text="East total is 120.5.", evidence_ids=["E9"])],
    )

    assert not validate_answer(invented, EVIDENCE)
    assert not validate_answer(unknown, EVIDENCE)
