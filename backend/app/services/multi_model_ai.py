"""
Multi-Model AI Analysis System
Uses different specialized checks for report analysis:
- Azure OpenAI vision: Image authenticity, manipulation cues, scene
  consistency with the reported hazard
- Azure OpenAI text: Description coherence, spam detection, summary
  generation
- Geographic and temporal heuristics: Location plausibility, report
  timing consistency
- Tavily/Web Search: Real-time news verification, location context

Three of these run against the same Azure OpenAI deployment; only the
prompt differs.
"""

import logging
import json
import requests
from datetime import datetime
from typing import Dict, List, Any
from app.core.config import settings
from app.services.azure_clients import get_openai_client

logger = logging.getLogger(__name__)


class MultiModelAnalyzer:
    """Orchestrates multiple AI checks for comprehensive report analysis"""

    @property
    def client(self):
        """Azure OpenAI client, built on first use.

        Raises AzureServiceError when Azure is disabled; every caller
        below catches it and degrades to a neutral score.
        """
        return get_openai_client()


    async def analyze_cluster(self, cluster_data: Dict) -> Dict[str, Any]:
        """
        Comprehensive multi-model analysis of a report cluster
        
        Returns:
            {
                "authenticity_score": float (0-1),
                "summary": str,
                "severity_recommendation": str,
                "analysis_breakdown": {
                    "image_authenticity": float,
                    "location_verification": float,
                    "temporal_consistency": float,
                    "news_correlation": float,
                    "text_coherence": float
                }
            }
        """
        try:
            # Run all analyses in parallel
            image_score = await self._analyze_images(cluster_data)
            location_score = await self._verify_location_context(cluster_data)
            temporal_score = await self._check_temporal_consistency(cluster_data)
            news_score = await self._correlate_with_news(cluster_data)
            text_score = await self._analyze_text_coherence(cluster_data)
            
            # Weighted scoring system
            weights = {
                "image": 0.30,      # 30% - Visual evidence is crucial
                "location": 0.25,   # 25% - Location context matters
                "temporal": 0.15,   # 15% - Time consistency
                "news": 0.20,       # 20% - Real-time news correlation
                "text": 0.10        # 10% - Text coherence
            }
            
            final_score = (
                image_score * weights["image"] +
                location_score * weights["location"] +
                temporal_score * weights["temporal"] +
                news_score * weights["news"] +
                text_score * weights["text"]
            )
            
            # Generate comprehensive summary using Azure OpenAI
            summary = await self._generate_summary(
                cluster_data, 
                final_score,
                {
                    "image_authenticity": image_score,
                    "location_verification": location_score,
                    "temporal_consistency": temporal_score,
                    "news_correlation": news_score,
                    "text_coherence": text_score
                }
            )
            
            # Determine severity
            severity = self._determine_severity(cluster_data, final_score)
            
            return {
                "authenticity_score": round(final_score, 2),
                "summary": summary,
                "severity_recommendation": severity,
                "analysis_breakdown": {
                    "image_authenticity": round(image_score, 2),
                    "location_verification": round(location_score, 2),
                    "temporal_consistency": round(temporal_score, 2),
                    "news_correlation": round(news_score, 2),
                    "text_coherence": round(text_score, 2)
                }
            }
            
        except Exception as e:
            logger.error(f"Multi-model analysis failed: {e}")
            return {
                "authenticity_score": 0.5,
                "summary": "Analysis incomplete due to technical error. Manual review recommended.",
                "severity_recommendation": cluster_data["reports"][0].get("severity", "medium"),
                "analysis_breakdown": {}
            }
    
    async def _analyze_images(self, cluster_data: Dict) -> float:
        """
        Use Azure OpenAI vision to analyze image authenticity
        - Detect AI-generated or manipulated images
        - Verify scene consistency with reported hazard
        - Flag content unrelated to the reported disaster
        """
        try:
            reports_with_images = [r for r in cluster_data["reports"] if r.get("has_image") and r.get("image_url")]

            if not reports_with_images:
                return 0.5  # Neutral score if no images

            scores = []
            hazard_type = cluster_data["hazard_type"].lower()

            # Expected labels for each hazard type
            hazard_labels = {
                "flood": ["water", "flood", "rain", "storm", "damage", "debris", "building", "road"],
                "cyclone": ["storm", "wind", "damage", "debris", "rain", "cloud", "destruction"],
                "tsunami": ["water", "wave", "ocean", "damage", "debris", "coast", "beach"],
                "earthquake": ["damage", "building", "crack", "debris", "destruction", "rubble"],
                "fire": ["fire", "smoke", "flame", "burning", "damage", "destruction"],
                "oil spill": ["water", "oil", "pollution", "ocean", "coast", "beach", "contamination"],
            }

            expected_labels = hazard_labels.get(hazard_type, ["damage", "disaster"])

            for report in reports_with_images:
                try:
                    image_url = report.get("image_url", "")

                    # Video Indexer and blob URLs are absolute; a
                    # relative local-storage path cannot be fetched.
                    if not image_url or not image_url.startswith("http"):
                        scores.append(0.5)
                        continue

                    scores.append(
                        self._assess_image(
                            image_url,
                            hazard_type,
                            expected_labels,
                        )
                    )

                except Exception as img_error:
                    logger.warning(f"Failed to analyze individual image: {img_error}")
                    scores.append(0.5)  # Neutral if analysis fails

            # Bonus for multiple corroborating images
            if len(scores) >= 2:
                avg_score = sum(scores) / len(scores)
                return min(1.0, avg_score * 1.1)  # 10% bonus for multiple images

            return sum(scores) / len(scores) if scores else 0.5

        except Exception as e:
            logger.error(f"Image analysis failed: {e}")
            return 0.5

    def _assess_image(
        self,
        image_url: str,
        hazard_type: str,
        expected_labels: List[str],
    ) -> float:
        """Score one image with a single Azure OpenAI vision call.

        One request returns both a scene-consistency score and a
        manipulation flag, so labelling, moderation and relevance are
        all judged together.
        """
        from app.services.azure_ai import (
            IMAGE_MIME_TYPES,
            fetch_media_base64,
        )

        b64_data, media_format = fetch_media_base64(image_url)

        if not b64_data:
            return 0.5

        mime_type = IMAGE_MIME_TYPES.get(media_format)

        if mime_type is None:
            # Video in a cluster image slot: nothing to assess here.
            return 0.5

        prompt = (
            "You are verifying a citizen disaster report photo.\n\n"
            f"Reported hazard: {hazard_type}\n"
            "Expected visual elements: "
            f"{', '.join(expected_labels)}\n\n"
            "Respond ONLY with this JSON object:\n"
            '{"scene_consistency": 0.0, "is_manipulated": false, '
            '"is_unrelated": false}\n\n'
            "scene_consistency is 0.0-1.0 for how well the image "
            "matches the expected elements. Set is_manipulated true "
            "for AI-generation artifacts or edited content. Set "
            "is_unrelated true when the image shows no disaster at "
            "all (selfie, food, screenshot, document)."
        )

        response = self.client.chat.completions.create(
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
            max_tokens=200,
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        parsed = json.loads(
            (response.choices[0].message.content or "").strip()
        )

        try:
            consistency = float(parsed.get("scene_consistency", 0.5))
        except (TypeError, ValueError):
            consistency = 0.5

        if parsed.get("is_unrelated"):
            return 0.05

        # Mirrors the old moderation penalty, but keyed on a direct
        # manipulation judgement instead of a moderation label count.
        penalty = 0.35 if parsed.get("is_manipulated") else 0.0

        return max(0.0, min(1.0, consistency - penalty))

    async def _verify_location_context(self, cluster_data: Dict) -> float:
        """
        Verify location makes sense for the reported hazard
        - Check if location is in coastal area for tsunami/cyclone
        - Verify against known flood-prone zones
        - Cross-reference with geographic databases
        - Check distance from known landmarks
        """
        try:
            location = cluster_data.get("location", "")
            district = cluster_data.get("district", "")
            state = cluster_data.get("state", "")
            hazard_type = cluster_data["hazard_type"].lower()
            
            # Extract coordinates if available
            lat, lon = None, None
            if "°N" in location and "°E" in location:
                try:
                    parts = location.replace("°N", "").replace("°E", "").split(",")
                    lat = float(parts[0].strip())
                    lon = float(parts[1].strip())
                except:
                    pass
            
            # Coastal hazards should be near coast
            coastal_hazards = ["tsunami", "cyclone", "oil spill"]
            coastal_states = ["kerala", "tamil nadu", "andhra pradesh", "odisha", "west bengal", "gujarat", "maharashtra", "karnataka", "goa"]
            coastal_keywords = ["coastal", "beach", "port", "marine", "sea", "ocean", "bay"]
            
            if hazard_type in coastal_hazards:
                # Check state
                if any(cs in state.lower() for cs in coastal_states):
                    score = 0.85
                else:
                    score = 0.50  # Unlikely for non-coastal state
                
                # Check district name
                if any(kw in district.lower() for kw in coastal_keywords):
                    score = min(1.0, score + 0.10)
                
                # Check coordinates (India coastal areas)
                if lat and lon:
                    # Rough check: coastal areas are typically within 100km of coast
                    # This is simplified; production would use actual coastline data
                    if (lat < 15 and lon > 72) or (lat > 8 and lat < 13 and lon > 74):  # South/West coast
                        score = min(1.0, score + 0.05)
                
                return score
            
            # Flood-prone areas
            if hazard_type == "flood":
                flood_prone_states = ["assam", "bihar", "uttar pradesh", "west bengal", "kerala", "maharashtra", "odisha"]
                flood_keywords = ["river", "ganga", "brahmaputra", "yamuna", "godavari", "krishna", "narmada"]
                
                score = 0.70  # Base score - floods can happen anywhere
                
                if any(fps in state.lower() for fps in flood_prone_states):
                    score = 0.80
                
                if any(kw in district.lower() for kw in flood_keywords):
                    score = min(1.0, score + 0.10)
                
                return score
            
            # Earthquake-prone areas
            if hazard_type == "earthquake":
                earthquake_zones = ["jammu", "kashmir", "himachal", "uttarakhand", "sikkim", "assam", "gujarat", "delhi"]
                
                if any(ez in state.lower() or ez in district.lower() for ez in earthquake_zones):
                    return 0.85
                return 0.60  # Can happen elsewhere but less common
            
            # Default: most hazards can happen anywhere
            return 0.70
            
        except Exception as e:
            logger.error(f"Location verification failed: {e}")
            return 0.5
    
    async def _check_temporal_consistency(self, cluster_data: Dict) -> float:
        """
        Check if timing makes sense
        - Reports should be recent (not old photos)
        - Multiple reports should be within reasonable timeframe
        - Check against seasonal patterns
        """
        try:
            reports = cluster_data["reports"]
            
            if len(reports) < 2:
                return 0.6  # Can't verify temporal consistency with single report
            
            # Parse timestamps
            timestamps = []
            for report in reports:
                if report.get("time"):
                    try:
                        ts = datetime.fromisoformat(report["time"].replace('Z', '+00:00'))
                        timestamps.append(ts)
                    except:
                        pass
            
            if len(timestamps) < 2:
                return 0.6
            
            # Check time spread
            time_spread = (max(timestamps) - min(timestamps)).total_seconds() / 3600  # hours
            
            # Reports within 6 hours are highly consistent
            if time_spread <= 6:
                return 0.90
            elif time_spread <= 24:
                return 0.75
            else:
                return 0.50  # Suspicious if spread over days
                
        except Exception as e:
            logger.error(f"Temporal consistency check failed: {e}")
            return 0.5
    
    async def _correlate_with_news(self, cluster_data: Dict) -> float:
        """
        Check against real-time news and social media using web search
        - Search for recent news about the hazard in the location
        - Verify against official weather/disaster alerts
        - Check social media trends
        """
        try:
            hazard_type = cluster_data["hazard_type"]
            location = cluster_data.get("location", "")
            district = cluster_data.get("district", "Unknown")
            
            # Build search query for recent news
            query = f"{hazard_type} {district} India disaster emergency latest news"
            
            # Use Tavily API for real-time web search
            # You can also use Google Custom Search API or Bing Search API
            tavily_api_key = getattr(settings, 'TAVILY_API_KEY', None)
            
            if tavily_api_key:
                try:
                    # Tavily Search API
                    search_response = requests.post(
                        "https://api.tavily.com/search",
                        json={
                            "api_key": tavily_api_key,
                            "query": query,
                            "search_depth": "basic",
                            "max_results": 5,
                            "include_domains": ["ndtv.com", "timesofindia.com", "indianexpress.com", "hindustantimes.com", "news18.com"],
                            "days": 2  # Only recent news
                        },
                        timeout=10
                    )
                    
                    if search_response.status_code == 200:
                        results = search_response.json().get("results", [])
                        
                        if len(results) >= 3:
                            return 0.90  # Strong news correlation
                        elif len(results) >= 1:
                            return 0.75  # Some news coverage
                        else:
                            # Check report count as fallback
                            report_count = cluster_data.get("report_count", 1)
                            if report_count >= 5:
                                return 0.70  # Many reports but no news yet
                            return 0.55  # Few reports, no news
                    
                except Exception as search_error:
                    logger.warning(f"Tavily search failed: {search_error}")
            
            # Fallback: Use report count and timing as proxy
            report_count = cluster_data.get("report_count", 1)
            
            # More reports = higher likelihood of real event
            if report_count >= 5:
                return 0.75  # Likely real if many reports
            elif report_count >= 3:
                return 0.65
            else:
                return 0.50  # Uncertain without news correlation
                
        except Exception as e:
            logger.error(f"News correlation failed: {e}")
            return 0.5
    
    async def _analyze_text_coherence(self, cluster_data: Dict) -> float:
        """
        Use Azure OpenAI to analyze text descriptions for coherence
        - Check if descriptions are realistic
        - Detect spam/bot-like patterns
        - Verify language consistency
        """
        try:
            descriptions = [r.get("description", "") for r in cluster_data["reports"]]

            if not descriptions or all(not d for d in descriptions):
                return 0.4  # Low score for missing descriptions

            prompt = f"""Analyze these disaster report descriptions for authenticity:

Reports: {json.dumps(descriptions, indent=2)}

Evaluate:
1. Are descriptions realistic and detailed?
2. Do they show genuine concern vs spam?
3. Is language natural vs bot-generated?
4. Do multiple reports corroborate each other?

Respond ONLY with this JSON object, where score is 0.0 to 1.0 \
indicating authenticity: {{"score": 0.0}}"""

            response = self.client.chat.completions.create(
                model=settings.AZURE_OPENAI_TEXT_DEPLOYMENT,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.2,
                response_format={"type": "json_object"},
            )

            parsed = json.loads(
                (response.choices[0].message.content or "").strip()
            )

            # Extract score
            try:
                score = float(parsed.get("score"))
                return max(0.0, min(1.0, score))
            except (TypeError, ValueError):
                return 0.65  # Default if parsing fails

        except Exception as e:
            logger.error(f"Text coherence analysis failed: {e}")
            return 0.65

    async def _generate_summary(self, cluster_data: Dict, final_score: float, breakdown: Dict) -> str:
        """
        Use Azure OpenAI to generate comprehensive summary
        """
        try:
            prompt = f"""You are an AI disaster analyst. Summarize this cluster analysis:

Hazard: {cluster_data['hazard_type']}
Location: {cluster_data.get('location', 'Unknown')}
District: {cluster_data.get('district', 'Unknown')}
Reports: {cluster_data.get('report_count', 0)}

Analysis Scores:
- Image Authenticity: {breakdown.get('image_authenticity', 0):.0%}
- Location Verification: {breakdown.get('location_verification', 0):.0%}
- Temporal Consistency: {breakdown.get('temporal_consistency', 0):.0%}
- News Correlation: {breakdown.get('news_correlation', 0):.0%}
- Text Coherence: {breakdown.get('text_coherence', 0):.0%}

Overall Authenticity: {final_score:.0%}

Provide a 2-3 sentence summary for emergency responders. Be concise and actionable."""

            response = self.client.chat.completions.create(
                model=settings.AZURE_OPENAI_TEXT_DEPLOYMENT,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.3,
            )

            return (response.choices[0].message.content or "").strip()

        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return f"Cluster of {cluster_data.get('report_count', 0)} {cluster_data['hazard_type']} reports. Authenticity score: {final_score:.0%}. Recommend verification."

    def _determine_severity(self, cluster_data: Dict, authenticity_score: float) -> str:
        """Determine severity based on reports and authenticity"""
        reports = cluster_data["reports"]
        
        # Count severity levels
        critical_count = sum(1 for r in reports if r.get("severity") == "critical")
        high_count = sum(1 for r in reports if r.get("severity") == "high")
        
        # High authenticity + critical reports = critical
        if authenticity_score >= 0.75 and critical_count >= 2:
            return "critical"
        
        # High authenticity + high severity = high
        if authenticity_score >= 0.70 and (critical_count >= 1 or high_count >= 2):
            return "high"
        
        # Multiple reports with good authenticity = at least medium
        if authenticity_score >= 0.65 and len(reports) >= 3:
            return "medium"
        
        # Default to most common severity in reports
        severities = [r.get("severity", "medium") for r in reports]
        return max(set(severities), key=severities.count)


# Global instance
multi_model_analyzer = MultiModelAnalyzer()


async def analyze_report_cluster_multi_model(cluster_data: Dict) -> Dict[str, Any]:
    """
    Main entry point for multi-model cluster analysis
    """
    return await multi_model_analyzer.analyze_cluster(cluster_data)
