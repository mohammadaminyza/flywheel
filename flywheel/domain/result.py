from typing import Any

from pydantic import BaseModel, Field


class AgentQuestion(BaseModel):
    question: str
    why: str = ""
    options: list[str] = Field(default_factory=list)


class TestsAdded(BaseModel):
    unit: list[str] = Field(default_factory=list)
    integration: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    status: str
    summary: str = ""
    branch: str | None = None
    questions: list[AgentQuestion] = Field(default_factory=list)
    tests_added: TestsAdded = Field(default_factory=TestsAdded)
    files_changed: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)

    @property
    def needs_input(self) -> bool:
        return self.status == "needs_input"

    @property
    def completed(self) -> bool:
        return self.status == "completed"


AGENT_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "needs_input", "failed"]},
        "summary": {"type": "string"},
        "branch": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question"],
            },
        },
        "tests_added": {
            "type": "object",
            "properties": {
                "unit": {"type": "array", "items": {"type": "string"}},
                "integration": {"type": "array", "items": {"type": "string"}},
            },
        },
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "follow_ups": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "summary"],
}
