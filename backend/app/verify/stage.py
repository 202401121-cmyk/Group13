"""Verify stub (US-52). US-16, US-40 and US-42 replace run() with real conflict checks; this becomes the fallback."""

from __future__ import annotations

from app.contracts import StageContext, StageName, VerifyInput, VerifyResult, examples


class VerifyStage:
    """Returns the example resolved values, including a Disputed, an Estimated and an Unknown one."""

    name = StageName.VERIFY
    is_placeholder = True

    def run(self, inp: VerifyInput, ctx: StageContext) -> VerifyResult:
        return examples.verify()

    def fallback(self, inp: VerifyInput, ctx: StageContext, error: Exception) -> VerifyResult:
        return VerifyResult.model_validate({**examples.verify().model_dump(), "trust_method": "tier_order"})
