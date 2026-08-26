from app.core.config import settings
from app.services.ai.base import AIProvider, AIProviderError
from app.services.ai.models import AIAnalysisRequest, AIAnalysisResult


class AzureProvider(AIProvider):
    name = "azure"

    def analyze(self, request: AIAnalysisRequest) -> AIAnalysisResult:
        if not settings.AZURE_ENABLED:
            raise AIProviderError(
                "Azure provider is disabled. Set AZURE_ENABLED=true to use "
                "Azure OpenAI."
            )

        try:
            # Import lazily so local-only startup never initializes Azure.
            from app.services.azure_ai import analyze_single_report

            raw_result = analyze_single_report(
                description=request.analysis_text,
                hazard_type=request.hazard_type,
                media_url=request.media_url,
                lat=request.latitude,
                lon=request.longitude,
                state=request.state,
            )

        except Exception as exc:
            raise AIProviderError(
                f"Azure OpenAI analysis failed: {exc}"
            ) from exc

        try:
            score = float(raw_result.get("authenticity_score", 0.5))
        except (TypeError, ValueError):
            score = 0.5

        score = max(0.0, min(1.0, score))

        recommended_status = raw_result.get(
            "recommended_status",
            "pending",
        )

        if recommended_status not in {"pending", "verified", "false"}:
            recommended_status = "pending"

        summary = raw_result.get(
            "preliminary_summary",
            "Azure OpenAI analysis completed.",
        )

        return AIAnalysisResult(
            provider=self.name,
            authenticity_score=score,
            summary=summary,
            recommended_status=recommended_status,
            details={
                "submitted_hazard_type": request.hazard_type,
                "azure_result": raw_result,
            },
        )
