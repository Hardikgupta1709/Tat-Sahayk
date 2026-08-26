"""Azure AI Video Indexer disaster-video verification.

Two properties of the API shape this module:

* Video Indexer fetches the video itself over the public internet by
  URL, so the media must live on the configured Blob account rather
  than on the app's local disk.
* Label confidences are 0-1 floats and label names are lowercase. Both
  are normalized to the 0-100 percentages and case-insensitive matching
  that the keyword tables and score arithmetic below expect.

Video Indexer has no Python SDK; this uses ``requests``, already a
dependency.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import requests

from app.core.config import settings
from app.services.azure_clients import parse_blob_url


logger = logging.getLogger(__name__)

API_ENDPOINT = "https://api.videoindexer.ai"

# Account access tokens last one hour. Re-mint a little early rather
# than risk a 401 mid-poll.
TOKEN_LIFETIME_SECONDS = 3300
POLL_INTERVAL_SECONDS = 10
REQUEST_TIMEOUT_SECONDS = 30

_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}

# Disaster-related labels to look for
DISASTER_LABELS = {
    'flood': ['Water', 'Flood', 'Rain', 'Storm', 'River', 'Ocean', 'Submerged'],
    'fire': ['Fire', 'Smoke', 'Flame', 'Burning', 'Ash', 'Explosion'],
    'cyclone': ['Storm', 'Wind', 'Tornado', 'Hurricane', 'Cyclone', 'Cloud'],
    'earthquake': ['Rubble', 'Debris', 'Collapsed', 'Damage', 'Destruction', 'Crack'],
    'tsunami': ['Wave', 'Ocean', 'Water', 'Flood', 'Coast', 'Beach'],
    'landslide': ['Mud', 'Soil', 'Rock', 'Debris', 'Mountain', 'Hill'],
    'oil_spill': ['Oil', 'Water', 'Ocean', 'Pollution', 'Spill'],
}

IRRELEVANT_LABELS = [
    'Person', 'Face', 'Selfie', 'Indoor', 'Room', 'Furniture',
    'Food', 'Meal', 'Laptop', 'Computer', 'Phone', 'Screen',
    'Text', 'Document', 'Book', 'Clothing', 'Fashion'
]

GENERAL_DISASTER_LABELS = [
    'Damage', 'Destruction', 'Emergency', 'Disaster', 'Rescue',
    'Evacuation',
]

# Video Indexer omits confidence on some labels. Assume a moderate
# confidence rather than discarding the label.
DEFAULT_LABEL_CONFIDENCE = 0.8


class VideoIndexerError(RuntimeError):
    """Raised when Video Indexer cannot be reached or is misconfigured."""


def is_indexable_media_url(url: str) -> bool:
    """Return True when Video Indexer can fetch this URL itself.

    Video Indexer downloads the video over the public internet, so a
    relative ``/uploads/...`` path from local media storage is not
    usable. Only blob URLs on the configured account qualify.
    """
    if not url:
        return False

    return parse_blob_url(url) is not None


def _require_configured() -> None:
    if not settings.video_indexer_configured:
        raise VideoIndexerError(
            "Azure AI Video Indexer is not configured. Set "
            "AZURE_VIDEO_INDEXER_ACCOUNT_ID, "
            "AZURE_VIDEO_INDEXER_LOCATION and "
            "AZURE_VIDEO_INDEXER_API_KEY."
        )


def _account_path() -> str:
    return (
        f"{settings.AZURE_VIDEO_INDEXER_LOCATION}/Accounts/"
        f"{settings.AZURE_VIDEO_INDEXER_ACCOUNT_ID}"
    )


def _get_access_token() -> str:
    """Return a cached account access token, minting one if needed."""
    _require_configured()

    now = time.monotonic()

    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    url = (
        f"{API_ENDPOINT}/Auth/"
        f"{settings.AZURE_VIDEO_INDEXER_LOCATION}/Accounts/"
        f"{settings.AZURE_VIDEO_INDEXER_ACCOUNT_ID}/AccessToken"
    )

    response = requests.get(
        url,
        params={"allowEdit": "true"},
        headers={
            "Ocp-Apim-Subscription-Key": (
                settings.AZURE_VIDEO_INDEXER_API_KEY
            ),
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        raise VideoIndexerError(
            "Video Indexer rejected the access token request "
            f"(HTTP {response.status_code}): {response.text[:300]}"
        )

    # The endpoint returns the JWT as a bare JSON string.
    token = response.json()

    if not isinstance(token, str) or not token:
        raise VideoIndexerError(
            "Video Indexer returned an unusable access token"
        )

    _token_cache["token"] = token
    _token_cache["expires_at"] = now + TOKEN_LIFETIME_SECONDS

    return token


def _video_name(video_url: str) -> str:
    """Derive a Video Indexer video name from the blob path.

    Video Indexer caps names at 80 characters.
    """
    tail = video_url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]

    return (tail or "report-video")[:80]


def _upload_video(video_url: str, access_token: str) -> str:
    """Submit a video by URL and return its Video Indexer ID."""
    params = {
        "accessToken": access_token,
        "name": _video_name(video_url),
        "privacy": "Private",
        "videoUrl": video_url,
    }

    if settings.AZURE_VIDEO_INDEXER_INDEXING_PRESET:
        params["indexingPreset"] = (
            settings.AZURE_VIDEO_INDEXER_INDEXING_PRESET
        )

    if settings.AZURE_VIDEO_INDEXER_STREAMING_PRESET:
        params["streamingPreset"] = (
            settings.AZURE_VIDEO_INDEXER_STREAMING_PRESET
        )

    response = requests.post(
        f"{API_ENDPOINT}/{_account_path()}/Videos",
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    if response.status_code >= 400:
        # A 400 naming indexingPreset or streamingPreset means the
        # configured value is not accepted for this account.
        raise VideoIndexerError(
            "Video Indexer rejected the upload "
            f"(HTTP {response.status_code}): {response.text[:300]}"
        )

    video_id = response.json().get("id")

    if not video_id:
        raise VideoIndexerError(
            "Video Indexer upload returned no video id"
        )

    logger.info("Video Indexer job started: %s", video_id)

    return video_id


def _fetch_index(video_id: str, access_token: str) -> Dict:
    response = requests.get(
        f"{API_ENDPOINT}/{_account_path()}/Videos/{video_id}/Index",
        params={"accessToken": access_token},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    if response.status_code >= 400:
        raise VideoIndexerError(
            "Video Indexer index request failed "
            f"(HTTP {response.status_code}): {response.text[:300]}"
        )

    return response.json()


def _index_state(index: Dict) -> str:
    state = index.get("state")

    if state:
        return str(state)

    videos = index.get("videos") or []

    if videos:
        return str(videos[0].get("state", "Unknown"))

    return "Unknown"


def _index_insights(index: Dict) -> Dict:
    videos = index.get("videos") or []

    if not videos:
        return {}

    return videos[0].get("insights") or {}


def analyze_video_for_disaster(
    media_url: str,
    hazard_type: str,
    lat: float,
    lon: float,
    state: str = None,
    max_wait_seconds: Optional[int] = None,
) -> Dict:
    """
    Analyze video using Azure AI Video Indexer with contextual
    verification. Combines visual analysis with weather, seasonal, and
    news data.

    Args:
        media_url: Publicly reachable blob URL of the video
        hazard_type: Expected disaster type (flood, fire, etc.)
        lat: Latitude of report location
        lon: Longitude of report location
        state: State/region for news checking
        max_wait_seconds: Maximum time to wait for indexing; defaults
            to AZURE_VIDEO_INDEXER_TIMEOUT_SECONDS

    Returns:
        Dictionary with authenticity analysis results including
        contextual verification
    """
    from app.services.context_verifier import (
        check_news_context,
        check_seasonal_context,
        check_weather_context,
    )

    if max_wait_seconds is None:
        max_wait_seconds = settings.AZURE_VIDEO_INDEXER_TIMEOUT_SECONDS

    # Run contextual checks first (fast, don't wait for indexing)
    logger.info(
        "Running contextual verification for video: %s at (%s, %s)",
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

    try:
        access_token = _get_access_token()

        logger.info(
            "Starting Video Indexer analysis for %s",
            media_url,
        )
        video_id = _upload_video(media_url, access_token)

        elapsed = 0

        while elapsed < max_wait_seconds:
            time.sleep(POLL_INTERVAL_SECONDS)
            elapsed += POLL_INTERVAL_SECONDS

            index = _fetch_index(video_id, access_token)
            index_state = _index_state(index)

            if index_state == "Processed":
                logger.info(
                    "Video Indexer job completed: %s",
                    video_id,
                )

                return _process_insights_with_context(
                    _index_insights(index),
                    hazard_type,
                    weather_ctx,
                    season_ctx,
                    news_ctx,
                )

            if index_state == "Failed":
                logger.error(
                    "Video Indexer job failed: %s",
                    video_id,
                )

                return _default_video_response_with_context(
                    "Video analysis failed",
                    weather_ctx,
                    season_ctx,
                    news_ctx,
                )

            logger.debug(
                "Video Indexer job %s state=%s (%ss)",
                video_id,
                index_state,
                elapsed,
            )

        logger.warning(
            "Video Indexer job %s timed out after %ss",
            video_id,
            max_wait_seconds,
        )

        return _default_video_response_with_context(
            "Video analysis timed out - manual review required",
            weather_ctx,
            season_ctx,
            news_ctx,
        )

    except Exception as exc:
        logger.error(
            "Error analyzing video with Video Indexer: %s",
            exc,
        )

        return _default_video_response_with_context(
            f"Video analysis error: {exc}",
            weather_ctx,
            season_ctx,
            news_ctx,
        )


def _label_confidences(insights: Dict) -> Dict[str, float]:
    """Collapse Video Indexer labels to ``{name: confidence 0-100}``.

    Video Indexer nests per-appearance instances under each label with
    0-1 confidences, so take each label's best instance and rescale to
    the percentages the scoring below expects.
    """
    confidences: Dict[str, float] = {}

    for label in insights.get("labels") or []:
        name = (label.get("name") or "").strip()

        if not name:
            continue

        best = 0.0
        seen_instance = False

        for instance in label.get("instances") or []:
            seen_instance = True
            value = instance.get("confidence")

            if value is None:
                value = DEFAULT_LABEL_CONFIDENCE

            try:
                best = max(best, float(value))
            except (TypeError, ValueError):
                continue

        if not seen_instance:
            best = DEFAULT_LABEL_CONFIDENCE

        percentage = best * 100.0

        if name not in confidences or percentage > confidences[name]:
            confidences[name] = percentage

    return confidences


def _process_insights_with_context(
    insights: Dict,
    hazard_type: str,
    weather_ctx: Dict,
    season_ctx: Dict,
    news_ctx: Dict,
) -> Dict:
    """Process Video Indexer insights and combine with context."""
    from app.services.context_verifier import calculate_combined_score

    label_confidences = _label_confidences(insights)

    if not label_confidences:
        # No visual content detected - rely on context
        final_score, status, summary = calculate_combined_score(
            visual_score=0.3,
            weather_ctx=weather_ctx,
            season_ctx=season_ctx,
            news_ctx=news_ctx,
            is_fake=False,
            is_relevant=False,
            location_plausible=True,
            hazard_type=hazard_type,
        )

        return {
            "is_disaster_relevant": False,
            "relevance_reason": (
                "No recognizable content detected in video"
            ),
            "is_fake": False,
            "fake_reason": None,
            "location_plausible": True,
            "location_reason": (
                "Unable to verify location from video"
            ),
            "authenticity_score": final_score,
            "summary": summary,
        }

    detected_labels = set(label_confidences.keys())
    logger.info("Detected labels: %s", detected_labels)

    # Video Indexer label names are lowercase, so every comparison
    # below is case-insensitive.
    lowered = {name.lower(): name for name in detected_labels}

    irrelevant_matches = [
        lowered[candidate.lower()]
        for candidate in IRRELEVANT_LABELS
        if candidate.lower() in lowered
    ]

    if len(irrelevant_matches) >= 3:
        final_score, status, summary = calculate_combined_score(
            visual_score=0.1,
            weather_ctx=weather_ctx,
            season_ctx=season_ctx,
            news_ctx=news_ctx,
            is_fake=False,
            is_relevant=False,
            location_plausible=True,
            hazard_type=hazard_type,
        )

        return {
            "is_disaster_relevant": False,
            "relevance_reason": (
                "Video shows non-disaster content: "
                + ", ".join(irrelevant_matches[:3])
            ),
            "is_fake": False,
            "fake_reason": None,
            "location_plausible": True,
            "location_reason": "Not applicable",
            "authenticity_score": final_score,
            "summary": summary,
        }

    # Check for disaster-related labels
    hazard_keywords = DISASTER_LABELS.get(hazard_type.lower(), [])
    disaster_matches: list[tuple[str, float]] = []

    for label in detected_labels:
        if any(
            keyword.lower() in label.lower()
            for keyword in hazard_keywords
        ):
            disaster_matches.append((label, label_confidences[label]))

    # Also check for general disaster indicators
    general_lowered = {
        candidate.lower() for candidate in GENERAL_DISASTER_LABELS
    }

    for label in detected_labels:
        if label.lower() in general_lowered:
            disaster_matches.append((label, label_confidences[label]))

    # Calculate visual score
    if not disaster_matches:
        visual_score = 0.25
        is_relevant = False
    else:
        num_matches = len(disaster_matches)
        avg_confidence = (
            sum(conf for _, conf in disaster_matches) / num_matches
        )

        # Base score from confidence (0.5 to 0.9 range)
        visual_score = 0.5 + (avg_confidence / 100.0) * 0.4

        # Bonus for multiple matching labels
        if num_matches >= 3:
            visual_score += 0.1
        elif num_matches >= 2:
            visual_score += 0.05

        visual_score = min(0.95, visual_score)
        is_relevant = True

    # Combine with contextual verification
    final_score, status, summary = calculate_combined_score(
        visual_score=visual_score,
        weather_ctx=weather_ctx,
        season_ctx=season_ctx,
        news_ctx=news_ctx,
        is_fake=False,
        is_relevant=is_relevant,
        location_plausible=True,
        hazard_type=hazard_type,
    )

    matched_labels_str = (
        ", ".join(
            f"{label} ({conf:.0f}%)"
            for label, conf in disaster_matches[:5]
        )
        if disaster_matches
        else "None"
    )

    return {
        "is_disaster_relevant": is_relevant,
        "relevance_reason": (
            f"Video shows {hazard_type}-related content: "
            f"{matched_labels_str}"
            if is_relevant
            else f"No {hazard_type} indicators detected"
        ),
        "is_fake": False,
        "fake_reason": None,
        "location_plausible": True,
        "location_reason": (
            "Location verification requires manual review"
        ),
        "authenticity_score": final_score,
        "summary": summary,
    }


def _default_video_response_with_context(
    message: str,
    weather_ctx: Dict,
    season_ctx: Dict,
    news_ctx: Dict,
) -> Dict:
    """Return a context-only response when indexing cannot complete."""
    from app.services.context_verifier import calculate_combined_score

    final_score, status, summary = calculate_combined_score(
        visual_score=0.5,
        weather_ctx=weather_ctx,
        season_ctx=season_ctx,
        news_ctx=news_ctx,
        is_fake=False,
        is_relevant=True,
        location_plausible=True,
        hazard_type="Unknown",
    )

    return {
        "is_disaster_relevant": True,
        "relevance_reason": message,
        "is_fake": False,
        "fake_reason": None,
        "location_plausible": True,
        "location_reason": "Unable to verify from video",
        "authenticity_score": final_score,
        "summary": f"{message} | {summary}",
    }
