import feedparser
import sys
import os
from datetime import datetime
from time import mktime

sys.path.append(os.getcwd())

import asyncio
from app.db.session import SessionLocal
from app.models.social import SocialPost
from app.api.v1.endpoints.ws import manager

# --- CONFIGURATION ---
# Only fetch disaster-related news specifically about India's coastal regions
RSS_FEEDS = [
    # Google News: "Cyclone India coast" (Last 24 hours)
    {"url": "https://news.google.com/rss/search?q=cyclone+india+coast+when:1d&hl=en-IN&gl=IN&ceid=IN:en", "source": "Google News"},
    # Google News: "Flood India coastal" (Last 24 hours)
    {"url": "https://news.google.com/rss/search?q=flood+india+coastal+when:1d&hl=en-IN&gl=IN&ceid=IN:en", "source": "Google News"},
    # Google News: "Tsunami India" (Last 24 hours)
    {"url": "https://news.google.com/rss/search?q=tsunami+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en", "source": "Google News"},
    # Google News: "Storm India Bay of Bengal" (Last 24 hours)
    {"url": "https://news.google.com/rss/search?q=storm+india+bay+bengal+when:1d&hl=en-IN&gl=IN&ceid=IN:en", "source": "Google News"},
    # Google News: "Oil spill India coast" (Last 24 hours)
    {"url": "https://news.google.com/rss/search?q=oil+spill+india+coast+when:1d&hl=en-IN&gl=IN&ceid=IN:en", "source": "Google News"},
    # Google News: "Earthquake India" (Last 24 hours)
    {"url": "https://news.google.com/rss/search?q=earthquake+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en", "source": "Google News"},
    {"url": "https://news.google.com/rss/search?q=IMD+cyclone+warning+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en", "source": "Google News"},
    {"url": "https://news.google.com/rss/search?q=NDRF+rescue+coastal+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en", "source": "Google News"},
    {"url": "https://news.google.com/rss/search?q=INCOIS+warning+bay+bengal+when:1d&hl=en-IN&gl=IN&ceid=IN:en", "source": "Google News"},
    {"url": "https://news.google.com/rss/search?q=coastal+disaster+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en", "source": "Google News"},
    # GDACS (Global Disaster Alert System) - Real-time official alerts
    {"url": "https://www.gdacs.org/xml/rss.xml", "source": "GDACS"}
]

BOOST_KEYWORDS = {
    # Indian disaster agencies (high value)
    'imd': 3, 'ndrf': 3, 'incois': 3, 'sdrf': 3, 'coast guard': 3, 'ndma': 3,
    'india meteorological': 3, 'national disaster': 3,
    # Indian coastal states (medium value)
    'kerala': 2, 'odisha': 2, 'tamil nadu': 2, 'gujarat': 2,
    'west bengal': 2, 'andhra pradesh': 2, 'maharashtra': 2, 'goa': 2,
    'karnataka': 2, 'bay of bengal': 2, 'arabian sea': 2, 'indian ocean': 2,
    'lakshadweep': 2, 'andaman': 2, 'konkan': 2, 'malabar': 2,
    # Indian cities (low-medium)
    'mumbai': 2, 'chennai': 2, 'kolkata': 2, 'visakhapatnam': 2,
    'kochi': 2, 'mangalore': 2, 'puri': 2, 'paradip': 2, 'bhubaneswar': 2,
    # General India
    'india': 1, 'indian': 1,
    # Hazard terms
    'cyclone': 1, 'flood': 1, 'tsunami': 1, 'storm surge': 1,
    'coastal erosion': 1, 'high tide': 1, 'tidal wave': 1,
    'landslide': 1, 'earthquake': 1, 'oil spill': 1,
}

PENALTY_KEYWORDS = {
    'nepal': -5, 'pakistan': -5, 'bangladesh': -3,
    'china': -5, 'japan': -5, 'usa': -5, 'america': -5,
    'europe': -5, 'africa': -5, 'australia': -5,
    'cricket': -10, 'ipl': -10, 'election': -10,
    'bollywood': -10, 'movie': -10, 'film': -10,
    'stock market': -5, 'sensex': -5, 'nifty': -5,
}

def relevance_score(title, summary):
    """Score content relevance to Indian coastal disasters. >= 3 means relevant."""
    content = (title + " " + summary).lower()
    score = 0
    for keyword, points in BOOST_KEYWORDS.items():
        if keyword in content:
            score += points
    for keyword, points in PENALTY_KEYWORDS.items():
        if keyword in content:
            score += points  # points are already negative
    return score

def harvest():
    db = SessionLocal()
    print("Starting Social Harvest...")

    count = 0
    filtered_count = 0
    
    for feed_info in RSS_FEEDS:
        print(f"   Reading feed: {feed_info['source']}...")
        feed = feedparser.parse(feed_info["url"])

        for entry in feed.entries:
            # 1. Check if we already have this post (Prevent Duplicates)
            exists = db.query(SocialPost).filter(SocialPost.url == entry.link).first()
            if exists:
                continue

            # 2. Parse Date
            published_time = datetime.now()
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published_time = datetime.fromtimestamp(mktime(entry.published_parsed))

            # 3. Clean Content
            content_text = entry.title
            summary = ""
            if hasattr(entry, "summary"):
                summary = entry.summary
                content_text += f"\n\n{summary}"

            # 4. Filter irrelevant content
            if relevance_score(entry.title, summary) < 3:
                filtered_count += 1
                continue

            # 5. Save to DB
            post = SocialPost(
                source=feed_info["source"],
                author=entry.get("source", {}).get("title", "Unknown"),
                content=content_text,
                url=entry.link,
                published_at=published_time
            )
            db.add(post)
            count += 1
    
    db.commit()
    db.close()
    
    if count > 0:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(manager.broadcast({"type": "new_social_post"}))
        except RuntimeError:
            asyncio.run(manager.broadcast({"type": "new_social_post"}))
            
    print(f"Harvest Complete. Added {count} new posts. Filtered out {filtered_count} irrelevant posts.")

if __name__ == "__main__":
    harvest()