from pydantic import BaseModel, Field


class SearchRequest(BaseModel):

    query: str = Field(
        ...,
        min_length=3,
        max_length=2000,
    )

    k: int = Field(
        default=10,
        ge=1,
        le=50,
    )

    domain: str | None = Field(
        default=None,
        pattern="^(constitution|statutes|judgements|all)?$",
    )


class PreviousTurn(BaseModel):
    """The question answered just before, and the sources shown with it (for follow-ups)."""

    question: str = Field(..., max_length=4000)
    chunk_ids: list[str] = Field(default_factory=list, max_length=20)


class AnswerRequest(BaseModel):

    query: str = Field(
        ...,
        min_length=3,
        max_length=4000,
    )

    # v2: the previous turn; the backend decides whether this question follows it up
    previous: PreviousTurn | None = None

    # research (default), summarise or advocate; a "SUMMARISE: ..." query prefix also works
    mode: str | None = Field(
        default=None,
        pattern="^(?i:research|summarise|summarize|advocate)$",
    )

    domain: str | None = Field(
        default=None,
        pattern="^(constitution|statutes|judgements|all)?$",
    )