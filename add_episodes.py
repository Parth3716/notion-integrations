import os, sys, time, requests
from dotenv import load_dotenv
load_dotenv()

TMDB_API_KEY = os.environ["TMDB_API_KEY"]
NOTION_TOKEN = os.environ["NOTION_API_KEY"]
SHOWS_DS = os.environ["NOTION_SHOWS_DATA_SOURCE_ID"]
EPISODES_DS = os.environ["NOTION_EPISODES_DATA_SOURCE_ID"]

notion = requests.Session()
notion.headers.update({
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2026-03-11",
    "Content-Type": "application/json",
})
tmdb = requests.Session()

def tmdb_get(path, **params):
    params["api_key"] = TMDB_API_KEY
    r = tmdb.get(f"https://api.themoviedb.org/3{path}", params=params)
    r.raise_for_status()
    return r.json()

def resolve_tmdb_id(identifier):
    if identifier.startswith("tt"):
        results = tmdb_get(f"/find/{identifier}", external_source="imdb_id")["tv_results"]
        if not results:
            raise ValueError(f"No TMDB show found for IMDb ID {identifier}")
        return results[0]["id"]
    return int(identifier)

def find_show_page(tmdb_id):
    body = {"filter": {"property": "TMDB ID", "number": {"equals": tmdb_id}}}
    r = notion.post(f"https://api.notion.com/v1/data_sources/{SHOWS_DS}/query", json=body)
    r.raise_for_status()
    results = r.json()["results"]
    if not results:
        raise ValueError(f"No Shows row with TMDB ID {tmdb_id} — run add_tvshow.py first.")
    return results[0]["id"]

if __name__ == "__main__":
    tmdb_id = resolve_tmdb_id(sys.argv[1])
    show_page_id = find_show_page(tmdb_id)
    details = tmdb_get(f"/tv/{tmdb_id}")
    seasons = [s["season_number"] for s in details["seasons"] if s["season_number"] > 0]

    count = 0
    for sn in seasons:
        season_data = tmdb_get(f"/tv/{tmdb_id}/season/{sn}")
        for ep in season_data["episodes"]:
            code = f"S{sn:02d}E{ep['episode_number']:02d}"
            title = f"{code} - {ep.get('name', '')}".strip(" -")
            props = {
                "Episode": {"title": [{"text": {"content": title}}]},
                "Show": {"relation": [{"id": show_page_id}]},
                "Season Number": {"number": sn},
                "Episode Number": {"number": ep["episode_number"]},
                "Watched": {"checkbox": False},
            }
            if ep.get("air_date"):
                props["Air Date"] = {"date": {"start": ep["air_date"]}}
            if ep.get("runtime"):
                props["Runtime (min)"] = {"number": ep["runtime"]}
            still = None
            if ep.get("still_path"):
                still = f"https://image.tmdb.org/t/p/w500{ep['still_path']}"
                props["Still URL"] = {"files": [{"name": "poster.jpg", "type": "external", "external": {"url": still}}]}
            payload = {"parent": {"type": "data_source_id", "data_source_id": EPISODES_DS}, "properties": props}
            if still:
                payload["cover"] = {"type": "external", "external": {"url": still}}
            notion.post("https://api.notion.com/v1/pages", json=payload)
            count += 1
            time.sleep(0.34)  # Notion's ~3 req/sec cap — this is a hard floor, not a knob to lower

    print(f"Added {count} episodes")