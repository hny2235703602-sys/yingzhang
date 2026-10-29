"""Validated contracts for the story-script stage (schema version 2.0)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    ),
]
# 分镜列的景别取值，与提示词和界面保持一致。
ShotSize = Literal["远景", "全景", "中景", "近景", "特写"]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ModelProfile(DomainModel):
    profile_id: Identifier = "online"
    provider: NonEmpty
    protocol: Literal["chat_completions", "responses"]
    base_url: NonEmpty
    model: NonEmpty
    api_key_env: str = "NOVEL_LLM_API_KEY"
    context_budget: int = Field(default=16000, ge=0)
    max_output_tokens: int = Field(default=4000, ge=0)
    timeout: float = Field(default=120, gt=0)
    retries: int = Field(default=2, ge=0, le=5)
    structured_output: bool = False
    stream: bool = True
    # 思考型模型（如 Qwen3.5）会把输出预算消耗在推理上，显式设为 none 可关闭。
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    enabled: bool = True

    def validate_budget(self) -> "ModelProfile":
        if (
            self.context_budget
            and self.max_output_tokens
            and self.max_output_tokens >= self.context_budget
        ):
            raise ValueError("max_output_tokens must be smaller than context_budget")
        return self

    def model_post_init(self, context: object) -> None:
        self.validate_budget()


class VisualProfile(DomainModel):
    character_id: Identifier
    name: NonEmpty
    appearance: NonEmpty
    costume: NonEmpty
    costume_version: Identifier = "costume_01"
    version: int = Field(default=1, ge=1)
    origin: NonEmpty = "改编设定"
    reference: str = ""
    reference_assets: list[Identifier] = Field(default_factory=list)


class CharacterImageAsset(DomainModel):
    asset_id: Identifier
    work_id: Identifier
    character_id: Identifier
    costume_version: Identifier
    purpose: Literal["primary", "side", "expression", "candidate"] = "candidate"
    filename: NonEmpty
    mime_type: Literal["image/png", "image/jpeg"]
    size_bytes: int = Field(gt=0, le=10_000_000)
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    profile_version: int = Field(ge=1)
    created_at: NonEmpty


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


class ScriptCharacter(DomainModel):
    """本章出场人物，外观供人物图片与后续章节复用。"""

    character_id: Identifier
    name: NonEmpty
    appearance: NonEmpty
    costume: str = "本章未区分服装"


class ScriptRow(DomainModel):
    """故事脚本一行：时长 · 分镜（景别＋运镜＋画面描述）· 动作 · 台词。"""

    duration_seconds: float = Field(default=5, gt=0, le=120, description="时长，单位秒")
    shot_size: ShotSize = Field(description="景别：远景、全景、中景、近景、特写")
    camera: NonEmpty = Field(description="运镜，例如 缓慢推进、固定、跟拍、轻微环绕")
    frame: NonEmpty = Field(description="画面描述：镜头里看到的人、物、环境与构图")
    action: NonEmpty = Field(description="动作：这一镜发生的事")
    dialogue: str = Field(default="", description="台词，含说话人；没有台词时为空字符串")
    character_ids: list[Identifier] = Field(
        default_factory=list, description="本镜出场人物的 character_id"
    )
    setting: str = Field(default="", description="场景与环境的一句话描述")
    source_paragraph_ids: list[Identifier] = Field(
        default_factory=list, description="本镜依据的原文段落编号"
    )


class ScriptBatch(DomainModel):
    """一次模型调用产出的连续分镜与人物外观。"""

    characters: list[ScriptCharacter] = Field(default_factory=list)
    shots: list[ScriptRow] = Field(default_factory=list)


class ScriptShot(ScriptRow):
    shot_id: Identifier
    order: int = Field(ge=1)
    generation_status: NonEmpty = "not_submitted"


class StoryScript(DomainModel):
    characters: list[ScriptCharacter] = Field(default_factory=list)
    shots: list[ScriptShot] = Field(default_factory=list)


class Review(DomainModel):
    issues: list[NonEmpty] = Field(default_factory=list)
