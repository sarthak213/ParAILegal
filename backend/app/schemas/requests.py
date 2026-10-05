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


class AnswerRequest(BaseModel):

    query: str = Field(
        ...,
        min_length=3,
        max_length=4000,
    )

    # research (default), summarise or advocate; a "SUMMARISE: ..." query prefix also works
    mode: str | None = Field(
        default=None,
        pattern="^(?i:research|summarise|summarize|advocate)$",
    )

    domain: str | None = Field(
        default=None,
        pattern="^(constitution|statutes|judgements|all)?$",
    )