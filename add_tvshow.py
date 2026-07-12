import os, sys, json, requests
from dotenv import load_dotenv
load_dotenv()

TMDB_API_KEY = os.environ["TMDB_API_KEY"]
NOTION_TOKEN = os.environ["NOTION_API_KEY"]
SHOWS_DS = os.environ["NOTION_SHOWS_DATA_SOURCE_ID"]

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

def add_show(d):
    poster = f"https://image.tmdb.org/t/p/w500{d['poster_path']}" if d.get("poster_path") else None

    properties = {
        "Name": {"title": [{"text": {"content": d["name"]}}]},
        "TMDB ID": {"number": d["id"]},
        "Genres": {"multi_select": [{"name": g["name"]} for g in d.get("genres", [])]},
        "Total Episodes": {"number": d.get("number_of_episodes")},
    }
    if d.get("status"):
        properties["Air Status"] = {"select": {"name": d["status"]}}
    if d.get("first_air_date"):
        properties["First Air Date"] = {"date": {"start": d["first_air_date"]}}
    if poster:
        properties["Poster URL"] = {"files": [{"name": "poster.jpg", "type": "external", "external": {"url": poster}}]}

    next_air = d.get("next_episode_to_air")
    if next_air and next_air.get("air_date"):
        properties["Upcoming Air Date"] = {"date": {"start": next_air["air_date"]}}
    if next_air:
        if next_air.get("name"):
            properties["Upcoming Episode Name"] = {"rich_text": [{"text": {"content": next_air["name"]}}]}
        properties["Upcoming Episode Code"] = {"rich_text": [{"text": {"content": f"S{next_air['season_number']:02d}E{next_air['episode_number']:02d}"}}]}
        if next_air.get("still_path"):
            properties["Upcoming Episode Still URL"] = {"files": [{"name": "poster.jpg", "type": "external", "external": {"url": f"https://image.tmdb.org/t/p/w500{next_air['still_path']}"}}]}

    payload = {"parent": {"type": "data_source_id", "data_source_id": SHOWS_DS}, "properties": properties}
    if poster:
        payload["cover"] = {"type": "external", "external": {"url": poster}}

    r = notion.post("https://api.notion.com/v1/pages", json=payload)
    print(r.status_code)
    if r.status_code != 200:
        print(json.dumps(r.json(), indent=2))

if __name__ == "__main__":
    tmdb_id = resolve_tmdb_id(sys.argv[1])
    details = tmdb_get(f"/tv/{tmdb_id}")
    add_show(details)
    print(f"Added: {details['name']} ({(details.get('first_air_date') or '????')[:4]})")