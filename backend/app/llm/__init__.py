"""The one LLM wrapper (US-26, US-46). The only package allowed to talk to an LLM provider (build rule 1).

from app.llm import build_llm_client
llm = build_llm_client()
result = llm.complete(
    LLMTask.EXTRACT_CLAIMS, SYSTEM_PROMPT, text, RawExtraction, data_class=DataClass.PUBLIC, difficulty=Difficulty.LONG
)

data_class says what the prompt carries (private: the shopper's words; reddit; public) and difficulty how big
the job is. The routes table in config/models.yaml turns the pair into a provider chain.
"""

from .client import InMemoryCallLog, WrapperLLMClient, build_llm_client
from .config import load_llm_config
from .data_block import wrap_as_data

__all__ = ["InMemoryCallLog", "WrapperLLMClient", "build_llm_client", "load_llm_config", "wrap_as_data"]
