import asyncio
from services.discovery.youtube import YouTubeDiscoveryService


class FakePage:
    id = "fitviet"
    niche = "fitness transformation"
    content_keywords = []
    blocked_keywords = ["music", "playlist", "remix", "songs"]
    language = "vi"


queries = [
    "body transformation fat to fit motivation",
    "calisthenics transformation before after",
    "gym transformation 30 days challenge",
    "weight loss transformation shocking",
    "skinny to muscular transformation",
]

svc = YouTubeDiscoveryService()


async def search_all():
    all_results = []
    for q in queries:
        page = FakePage()
        page.content_keywords = q.split()[:3]
        try:
            results = await svc.find_for_page(page, max_results=5)
            all_results.extend(results)
        except Exception as e:
            print(f"Error for query '{q}': {e}")
    return all_results


results = asyncio.run(search_all())

seen = set()
unique = []
for r in results:
    url = r.get("source_url", "")
    if url not in seen:
        seen.add(url)
        unique.append(r)

unique.sort(
    key=lambda x: int((x.get("raw_metadata") or {}).get("view_count") or 0),
    reverse=True,
)

print(f"\nTop {min(10, len(unique))} fitness videos found:\n")
for r in unique[:10]:
    meta = r.get("raw_metadata", {})
    vc = meta.get("view_count", 0)
    dur = meta.get("duration", 0)
    title = r.get("source_title", "?")[:70]
    url = r.get("source_url", "")
    print(f"  {vc:>12,} views | {dur//60}m | {title}")
    print(f"  {url}")
    print()
