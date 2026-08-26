"""Azure OpenAI report verification.

A single ``gpt-4o-mini`` deployment serves both the vision forensics call
and the cluster text synthesis, so ``AZURE_OPENAI_VISION_DEPLOYMENT`` and
``AZURE_OPENAI_TEXT_DEPLOYMENT`` normally name the same deployment.

Clients are only reached through ``azure_clients``, which builds them
lazily. Importing this module never initializes an Azure SDK.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional

import requests

from app.core.config import settings
from app.services.azure_clients import (
    AzureServiceError,
    get_blob_service_client,
    get_openai_client,
    parse_blob_url,
)


logger = logging.getLogger(__name__)

# File extension -> the format name the rest of this module works in.
MEDIA_FORMATS = {
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "png": "png",
    "gif": "gif",
    "webp": "webp",
    "mp4": "video",
    "mov": "video",
    "webm": "video",
}

# Format name -> MIME type for the ``data:`` URI the vision call needs.
IMAGE_MIME_TYPES = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}

MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 10
VISION_MAX_TOKENS = 500

# A truncated response is unparseable JSON even with response_format
# set, and the cluster prompt asks for a full paragraph, so this needs
# more headroom than the vision limit above.
CLUSTER_MAX_TOKENS = 400

REQUIRED_VISION_FIELDS = (
    "is_disaster_relevant",
    "is_fake",
    "location_plausible",
    "authenticity_score",
    "summary",
)

FORENSIC_PROMPT = """You are a disaster verification expert for an \
Indian emergency response system.

A citizen submitted a report claiming: "{hazard_type}" at coordinates \
{lat}, {lon} (India).

Analyze the provided image and answer in this EXACT JSON format only:
{{
  "is_disaster_relevant": true,
  "relevance_reason": "one sentence",
  "is_fake": false,
  "fake_reason": null,
  "location_plausible": true,
  "location_reason": "one sentence",
  "authenticity_score": 0.0,
  "summary": "one sentence final verdict"
}}

SCORING RULES:
- Image shows NO disaster (laptop, selfie, food, fabric, unrelated) \
-> is_disaster_relevant: false, authenticity_score: 0.05
- AI-generated artifacts or clearly fake -> is_fake: true, \
authenticity_score: 0.10
- Disaster type impossible for that geography -> \
location_plausible: false, authenticity_score: 0.15
- Genuine disaster image with realistic damage -> \
authenticity_score: 0.75 to 0.95
- Only score above 0.70 if image CLEARLY shows flood, fire, earthquake \
damage, storm, oil spill, industrial accident

Respond ONLY with the JSON object."""


def _result(
    score: float,
    summary: str,
    status: str,
) -> dict[str, Any]:
    return {
        "authenticity_score": round(score, 2),
        "preliminary_summary": summary,
        "recommended_status": status,
    }


def _media_format_from_url(url: str) -> str:
    """Guess the media format from a URL, ignoring any query string."""
    path = url.split("?")[0]

    if "." not in path:
        return "jpeg"

    extension = path.rsplit(".", 1)[-1].lower()

    return MEDIA_FORMATS.get(extension, "jpeg")


def _strip_code_fence(text: str) -> str:
    """Remove a Markdown JSON fence if the model added one anyway."""
    return (
        text.replace("```json", "")
        .replace("```", "")
        .strip()
    )


def fetch_media_base64(
    url: str,
) -> tuple[Optional[str], Optional[str]]:
    """Download media and return ``(base64_payload, media_format)``.

    Media on the configured Blob account is downloaded with credentials
    so private containers work. Anything else falls back to a plain HTTP
    GET, which also covers a public blob URL when no key is configured.
    """
    if not url:
        return None, None

    media_format = _media_format_from_url(url)
    blob_location = parse_blob_url(url)

    if blob_location is not None:
        container, blob_name = blob_location

        try:
            blob_client = (
                get_blob_service_client().get_blob_client(
                    container=container,
                    blob=blob_name,
                )
            )
            content = blob_client.download_blob().readall()
        except AzureServiceError as exc:
            logger.warning(
                "Blob client unavailable, trying public URL: %s",
                exc,
            )
        except Exception as exc:
            logger.warning(
                "Blob download failed, trying public URL: %s",
                exc,
            )
        else:
            logger.info(
                "Fetched media from blob %s/%s",
                container,
                blob_name,
            )
            payload = base64.b64encode(content).decode("utf-8")

            return payload, media_format

    try:
        response = requests.get(
            url,
            timeout=MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.error("Media fetch failed for %s: %s", url, exc)
        return None, None

    if response.status_code != 200:
        logger.error(
            "HTTP %s when fetching %s",
            response.status_code,
            url,
        )
        return None, None

    logger.info("Fetched media from public URL")
    payload = base64.b64encode(response.content).decode("utf-8")

    return payload, media_format


def ask_forensic_vision_expert(
    hazard_type: str,
    lat: float,
    lon: float,
    b64_data: str,
    media_type: str,
) -> dict[str, Any]:
    """Run visual forensics on an image with Azure OpenAI vision.

    Raises on failure so the caller can degrade to context-only
    scoring.
    """
    if media_type == "video":
        raise ValueError(
            "Video media must be routed to azure_video_indexer, "
            "not the vision deployment"
        )

    mime_type = IMAGE_MIME_TYPES.get(media_type)

    if mime_type is None:
        raise ValueError(
            f"Unsupported media format for vision analysis: "
            f"{media_type}"
        )

    prompt = FORENSIC_PROMPT.format(
        hazard_type=hazard_type,
        lat=lat,
        lon=lon,
    )

    try:
        response = get_openai_client().chat.completions.create(
            model=settings.AZURE_OPENAI_VISION_DEPLOYMENT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{mime_type};base64,"
                                    f"{b64_data}"
                                ),
                            },
                        },
                    ],
                }
            ],
            max_tokens=VISION_MAX_TOKENS,
            temperature=0.1,
            # Enforces valid JSON, so the prompt does not have to ask
            # for it and hope.
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.error("Azure OpenAI vision call failed: %s", exc)
        raise

    text = _strip_code_fence(
        (response.choices[0].message.content or "")
    )

    if not text:
        raise ValueError(
            "Azure OpenAI returned an empty vision response"
        )

    try:
        parsed_result = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error(
            "Failed to parse Azure OpenAI JSON response: %s",
            exc,
        )
        raise ValueError(
            f"Azure OpenAI returned invalid JSON: {exc}"
        ) from exc

    if not isinstance(parsed_result, dict):
        raise ValueError(
            "Azure OpenAI vision response was not a JSON object"
        )

    for field in REQUIRED_VISION_FIELDS:
        if field not in parsed_result:
            raise ValueError(
                "Missing required field in Azure OpenAI response: "
                f"{field}"
            )

    return parsed_result


def _analyze_video(
    media_url: str,
    hazard_type: str,
    lat: float,
    lon: float,
    state: Optional[str],
    context_score: float,
    context_notes: str,
) -> dict[str, Any]:
    """Score a video with Azure AI Video Indexer.

    Falls back to context-only scoring when indexing is disabled, the
    URL is not reachable by Video Indexer, or the call fails.
    """
    if not settings.AZURE_VIDEO_INDEXER_ENABLED:
        logger.info(
            "Video indexing is disabled; scoring on context only"
        )

        return _result(
            context_score * 0.6,
            f"Video analysis not enabled. Context: {context_notes}",
            "pending",
        )

    try:
        from app.services.azure_video_indexer import (
            analyze_video_for_disaster,
            is_indexable_media_url,
        )

        if not is_indexable_media_url(media_url):
            logger.warning(
                "Video URL is not reachable by Video Indexer: %s",
                media_url,
            )

            return _result(
                context_score * 0.5,
                "Video URL format not supported. Context: "
                f"{context_notes}",
                "pending",
            )

        video_result = analyze_video_for_disaster(
            media_url,
            hazard_type,
            lat,
            lon,
            state,
        )
        score = video_result.get("authenticity_score", 0.5)

        # Video analysis already folds in the contextual checks.
        return {
            "authenticity_score": score,
            "preliminary_summary": video_result.get(
                "summary",
                "Video analysis complete",
            ),
            "recommended_status": (
                "pending" if score > 0.25 else "false"
            ),
        }

    except Exception as video_error:
        logger.error(
            "Video analysis failed: %s",
            video_error,
            exc_info=True,
        )

        return _result(
            context_score * 0.6,
            f"Video analysis failed. Context: {context_notes}",
            "pending",
        )


def analyze_single_report(
    description: str,
    hazard_type: str,
    media_url: Optional[str],
    lat: float,
    lon: float,
    state: Optional[str] = None,
) -> dict[str, Any]:
    """Multi-layered disaster report verification combining:

    1. Real-time weather data
    2. Seasonal patterns
    3. News corroboration
    4. Visual forensics (Azure OpenAI vision)

    Gracefully degrades to context-only scoring if visual analysis
    fails.
    """
    del description  # Text coherence is scored by the local provider.

    from app.services.context_verifier import (
        calculate_combined_score,
        check_news_context,
        check_seasonal_context,
        check_weather_context,
    )

    context_score = 0.5

    try:
        # --- Layer 1: Context checks (run even without an image) ---
        logger.info(
            "Running contextual verification for %s at (%s, %s)",
            hazard_type,
            lat,
            lon,
        )

        weather_ctx = check_weather_context(lat, lon, hazard_type)
        season_ctx = check_seasonal_context(hazard_type)
        news_ctx = check_news_context(lat, lon, hazard_type, state)

        logger.info(
            "Context checks: Weather=%s, Season=%s, News=%s",
            weather_ctx["weather_score"],
            season_ctx["season_score"],
            news_ctx["news_score"],
        )

        context_score = (
            weather_ctx["weather_score"] * 0.4
            + season_ctx["season_score"] * 0.3
            + news_ctx["news_score"] * 0.3
        )
        context_notes = " | ".join(
            (
                weather_ctx["note"],
                season_ctx["note"],
                news_ctx["note"],
            )
        )

        if not media_url:
            # No image — rely on context only, with a penalty.
            final_score = context_score * 0.6

            return _result(
                final_score,
                f"No image provided. Context: {context_notes}",
                "pending" if final_score > 0.3 else "false",
            )

        # --- Layer 2: Visual forensics ---
        logger.info("Fetching media from %s", media_url)
        b64_data, media_type = fetch_media_base64(media_url)

        if not b64_data:
            return _result(
                context_score * 0.5,
                f"Image fetch failed. Context: {context_notes}",
                "pending",
            )

        if media_type == "video":
            return _analyze_video(
                media_url=media_url,
                hazard_type=hazard_type,
                lat=lat,
                lon=lon,
                state=state,
                context_score=context_score,
                context_notes=context_notes,
            )

        logger.info(
            "Running visual forensics with deployment %s",
            settings.AZURE_OPENAI_VISION_DEPLOYMENT,
        )

        try:
            vision = ask_forensic_vision_expert(
                hazard_type,
                lat,
                lon,
                b64_data,
                media_type,
            )

            visual_score = vision.get("authenticity_score", 0.3)
            is_fake = vision.get("is_fake", False)
            is_relevant = vision.get("is_disaster_relevant", True)
            location_plausible = vision.get(
                "location_plausible",
                True,
            )

            logger.info(
                "Visual analysis: score=%s, fake=%s, relevant=%s, "
                "location_ok=%s",
                visual_score,
                is_fake,
                is_relevant,
                location_plausible,
            )

            # --- Layer 3: Combined weighted score ---
            final_score, status, summary = calculate_combined_score(
                visual_score=visual_score,
                weather_ctx=weather_ctx,
                season_ctx=season_ctx,
                news_ctx=news_ctx,
                is_fake=is_fake,
                is_relevant=is_relevant,
                location_plausible=location_plausible,
                hazard_type=hazard_type,
            )

            logger.info(
                "Final authenticity score: %s (%s)",
                final_score,
                status,
            )

            return {
                "authenticity_score": final_score,
                "preliminary_summary": summary,
                "recommended_status": status,
            }

        except Exception as visual_error:
            # Visual analysis failed — fall back to context only.
            logger.error(
                "Visual analysis failed: %s",
                visual_error,
                exc_info=True,
            )
            logger.info("Falling back to context-only scoring")

            # 40% penalty for missing visual verification.
            final_score = context_score * 0.6
            summary = " | ".join(
                (
                    "Visual analysis unavailable (Azure OpenAI "
                    "error)",
                    context_notes,
                )
            )

            # Anything above the floor still needs a human review.
            status = "false" if final_score < 0.25 else "pending"

            logger.info(
                "Context-only score: %s (%s)",
                final_score,
                status,
            )

            return _result(final_score, summary, status)

    except Exception as exc:
        # Catastrophic failure — even the context checks failed.
        logger.error(
            "Complete analysis failure: %s",
            exc,
            exc_info=True,
        )

        return {
            "authenticity_score": 0.3,
            "preliminary_summary": (
                f"Analysis system error: {exc} — manual review "
                "required"
            ),
            "recommended_status": "pending",
        }


def analyze_report_cluster(reports_data: list) -> dict:
    """Synthesize a cluster of verified reports into one summary."""
    if not reports_data:
        return {
            "cluster_summary": "No data.",
            "severity": "LOW",
            "confidence": 0.0,
        }

    prompt = (
        f"Analyze these {len(reports_data)} verified disaster "
        "reports from the same geographic cluster:\n"
    )

    for report in reports_data:
        prompt += (
            f"- Type: {report.get('hazard_type', 'Unknown')}, "
            f"Desc: {report.get('description', '')}\n"
        )

    prompt += (
        '\nOutput ONLY JSON: {"cluster_summary": '
        '"<1 paragraph synthesis>", "severity": '
        '"<LOW|MEDIUM|HIGH|CRITICAL>", "confidence": '
        "<float 0.0-1.0>}"
    )

    try:
        response = get_openai_client().chat.completions.create(
            model=settings.AZURE_OPENAI_TEXT_DEPLOYMENT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=CLUSTER_MAX_TOKENS,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        text = _strip_code_fence(
            (response.choices[0].message.content or "")
        )

        return json.loads(text)

    except Exception as exc:
        logger.error("Cluster analysis failed: %s", exc)

        return {
            "cluster_summary": "Analysis failed.",
            "severity": "MEDIUM",
            "confidence": 0.5,
        }
