import asyncio, json

from services.discovery.youtube import YouTubeDiscoveryService
from services.discovery.ranking import rank_candidates


class FakePage:
    id = "test"
    niche = "wilderness survival documentary"
    content_keywords = ["survival challenge", "survival story"]
    blocked_keywords = ["music", "playlist"]
    language = "vi"


svc = YouTubeDiscoveryService()
page = FakePage()
candidates = asyncio.run(svc.find_for_page(page, max_results=3))
print(f"Got {len(candidates)} candidates")
for c in candidates[:3]:
    meta = c.get("raw_metadata", {})
    title = c.get("source_title", "?")
    vc = meta.get("view_count")
    ud = meta.get("upload_date")
    dur = meta.get("duration")
    ds = c.get("duration_seconds")
    print(f"Title: {title}")
    print(f"  view_count={vc}, upload_date={ud}, duration={dur}, duration_seconds={ds}")
    print()

# Now test ranking
ranked = rank_candidates(candidates, topic="wilderness survival")
print(f"\nAfter ranking: {len(ranked)} candidates")
for r in ranked:
    meta = r.get("raw_metadata", {})
    print(f"  {r.get('source_title', '?')} -> score={meta.get('virality_score')}")
