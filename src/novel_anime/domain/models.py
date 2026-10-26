"""Validated contracts shared by adaptation stages (schema version 1)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    ),
]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ModelProfile(DomainModel):
    profile_id: Identifier = "online"
    provider: NonEmpty
    protocol: Literal["chat_completions", "responses"]
    base_url: NonEmpty
    model: NonEmpty
    api_key_env: str = "NOVEL_LLM_API_KEY"
    context_budget: int = Field(default=16000, gt=0)
    max_output_tokens: int = Field(default=4000, gt=0)
    timeout: float = Field(default=120, gt=0)
    retries: int = Field(default=2, ge=0, le=5)
    structured_output: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_budget(self) -> "ModelProfile":
        if self.max_output_tokens >= self.context_budget:
            raise ValueError("max_output_tokens must be smaller than context_budget")
        return self


class VisualProfile(DomainModel):
    character_id: Identifier
    name: NonEmpty
    appearance: NonEmpty
    costume: NonEmpty
    costume_version: Identifier = "costume_01"
    version: int = Field(default=1, ge=1)
    origin: NonEmpty = "改编设定"
    reference: str = ""


class InputDocument(DomainModel):
    work_id: Identifier
    title: NonEmpty
    text: NonEmpty
    context: str = ""
    style: NonEmpty = "二维动画"
    aspect_ratio: NonEmpty = "16:9"
    preference: str = ""
    visual_profiles: list[VisualProfile] = Field(default_factory=list)


class SourceParagraph(DomainModel):
    paragraph_id: Identifier
    text: NonEmpty


class Character(DomainModel):
    character_id: Identifier
    name: NonEmpty
    description: NonEmpty
    source_paragraph_ids: list[Identifier] = Field(default_factory=list)


class StoryEvent(DomainModel):
    event_id: Identifier
    description: NonEmpty
    source_paragraph_ids: list[Identifier] = Field(default_factory=list)
    importance: Literal["must", "condense", "omit", "uncertain"]
    reason: str = ""


class Analysis(DomainModel):
    summary: NonEmpty
    core_conflict: str = ""
    characters: list[Character] = Field(default_factory=list)
    events: list[StoryEvent] = Field(default_factory=list)
    uncertainties: list[NonEmpty] = Field(default_factory=list)
    relations: list[NonEmpty] = Field(default_factory=list)


class Scene(DomainModel):
    scene_id: Identifier
    title: NonEmpty
    location: NonEmpty
    time: NonEmpty
    script: NonEmpty
    event_ids: list[Identifier] = Field(default_factory=list)


class Outline(DomainModel):
    synopsis: NonEmpty
    changes: list[NonEmpty] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)


class Shot(DomainModel):
    shot_id: Identifier
    scene_id: Identifier
    order: int = Field(ge=1)
    title: NonEmpty
    source_paragraph_ids: list[Identifier] = Field(default_factory=list)
    source_event_ids: list[Identifier] = Field(default_factory=list)
    character_ids: list[Identifier] = Field(default_factory=list)
    story_purpose: NonEmpty
    action: NonEmpty
    shot_size: NonEmpty
    camera: NonEmpty
    composition: str = ""
    start_state: NonEmpty
    end_state: NonEmpty
    continuity_in: dict[str, str] = Field(default_factory=dict)
    continuity_out: dict[str, str] = Field(default_factory=dict)
    duration_seconds: float = Field(default=5, gt=0)
    dialogue: list[NonEmpty] = Field(default_factory=list)
    requires_dialogue: bool = False
    requires_text_overlay: bool = False
    visual_assumptions: list[NonEmpty] = Field(default_factory=list)
    depends_on_shot_ids: list[Identifier] = Field(default_factory=list)
    video_prompt: NonEmpty
    negative_prompt: str | None = None
    uncertainties: list[NonEmpty] = Field(default_factory=list)
    generation_status: NonEmpty = "not_submitted"


class ShotBatch(DomainModel):
    shots: list[Shot] = Field(default_factory=list)


class Review(DomainModel):
    issues: list[NonEmpty] = Field(default_factory=list)
