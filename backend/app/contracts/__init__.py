"""Stage contracts: the only way ShopSense stages talk to each other.

Import from here, not from the submodules:

    from app.contracts import Intent, Rubric, SearchResult
"""

from .claims import Claim, ExtractInput, ExtractionResult, ProductMention
from .commerce import PRICE_STALE_AFTER, CommerceInput, CommerceResult, Offer
from .common import (
    SCHEMA_VERSION,
    SEARCH_TIME_LIMIT_S,
    STAGE_TIME_LIMITS_S,
    ClaimType,
    Contract,
    Money,
    SourceTier,
    SourceType,
    StageName,
    StageReport,
    StageStatus,
)
from .criteria import CriteriaInput, CriteriaSet, Criterion, Direction
from .decide import Contribution, DecideInput, DecideResult, FilteredProduct, RankedProduct
from .enrich import EnrichInput, EnrichResult, EnrichTarget
from .entities import MatchInput, MatchResult, Product, Variant
from .explain import ExplainInput, ExplainResult, Explanation, ExplanationSentence
from .intake import Budget, Exclusion, HardRule, IntakeInput, Intent
from .interview import (
    MAX_QUESTIONS,
    MAX_UNCLEAR_PER_QUESTION,
    AskedQuestion,
    InterviewQuestion,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    RememberedPreference,
    ReplyClassification,
    ReplyType,
)
from .llm import (
    PERSONAL_TASKS,
    DataClass,
    Difficulty,
    LLMCallRecord,
    LLMClient,
    LLMTask,
    LLMUnavailable,
    ModelLimits,
    Provider,
)
from .pipeline import ProductSummary, ProgressEvent, SearchRequest, SearchResult, Stage, StageContext
from .research import RedditOrigin, ResearchInput, ResearchResult, Source, SourceFailure
from .rubric import Rubric, WeightedCriterion, normalize
from .verify import CommunitySignal, ResolvedValue, ValueState, VerifyInput, VerifyResult

__all__ = [
    "MAX_QUESTIONS",
    "MAX_UNCLEAR_PER_QUESTION",
    "PERSONAL_TASKS",
    "PRICE_STALE_AFTER",
    "SCHEMA_VERSION",
    "SEARCH_TIME_LIMIT_S",
    "STAGE_TIME_LIMITS_S",
    "AskedQuestion",
    "Budget",
    "Claim",
    "ClaimType",
    "CommerceInput",
    "CommerceResult",
    "CommunitySignal",
    "Contract",
    "Contribution",
    "CriteriaInput",
    "CriteriaSet",
    "Criterion",
    "DataClass",
    "DecideInput",
    "DecideResult",
    "Difficulty",
    "Direction",
    "EnrichInput",
    "EnrichResult",
    "EnrichTarget",
    "Exclusion",
    "ExplainInput",
    "ExplainResult",
    "Explanation",
    "ExplanationSentence",
    "ExtractInput",
    "ExtractionResult",
    "FilteredProduct",
    "HardRule",
    "IntakeInput",
    "Intent",
    "InterviewQuestion",
    "InterviewState",
    "InterviewStep",
    "InterviewTurn",
    "LLMCallRecord",
    "LLMClient",
    "LLMTask",
    "LLMUnavailable",
    "MatchInput",
    "MatchResult",
    "ModelLimits",
    "Money",
    "Offer",
    "Product",
    "ProductMention",
    "ProductSummary",
    "ProgressEvent",
    "Provider",
    "RankedProduct",
    "RedditOrigin",
    "RememberedPreference",
    "ReplyClassification",
    "ReplyType",
    "ResearchInput",
    "ResearchResult",
    "ResolvedValue",
    "Rubric",
    "SearchRequest",
    "SearchResult",
    "Source",
    "SourceFailure",
    "SourceTier",
    "SourceType",
    "Stage",
    "StageContext",
    "StageName",
    "StageReport",
    "StageStatus",
    "ValueState",
    "Variant",
    "VerifyInput",
    "VerifyResult",
    "WeightedCriterion",
    "normalize",
]
