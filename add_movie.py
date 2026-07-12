import os, sys, json, requests
from dotenv import load_dotenv
load_dotenv()

TMDB_API_KEY = os.environ["TMDB_API_KEY"]
NOTION_TOKEN = os.environ["NOTION_API_KEY"]
MOVIES_DS = os.environ["NOTION_MOVIES_DATA_SOURCE_ID"]

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2026-03-11",
    "Content-Type": "application/json",
}

def resolve_tmdb_id(identifier):
    if identifier.startswith("tt"):
        r = requests.get(f"https://api.themoviedb.org/3/find/{identifier}",
                          params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"})
        r.raise_for_status()
        results = r.json()["movie_results"]
        if not results:
            raise ValueError(f"No TMDB movie found for IMDb ID {identifier}")
        return results[0]["id"]
    return int(identifier)

def get_movie_details(tmdb_id):
    r = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}", params={"api_key": TMDB_API_KEY})
    r.raise_for_status()
    return r.json()

def add_movie(d):
    poster = f"https://image.tmdb.org/t/p/w500{d['poster_path']}" if d.get("poster_path") else None

    properties = {
        "Name": {"title": [{"text": {"content": d["title"]}}]},
        "Status": {"select": {"name": "Not Watched"}},
        "Genres": {"multi_select": [{"name": g["name"]} for g in d.get("genres", [])]},
        "Runtime (min)": {"number": d.get("runtime")},
        "Overview": {"rich_text": [{"text": {"content": d.get("overview", "")}}]},
    }
    if d.get("release_date"):
        properties["Release Date"] = {"date": {"start": d["release_date"]}}
    if poster:
        properties["Poster URL"] = {"files": [{"name": "poster.jpg", "type": "external", "external": {"url": poster}}]}

    payload = {"parent": {"type": "data_source_id", "data_source_id": MOVIES_DS}, "properties": properties}
    if poster:
        payload["cover"] = {"type": "external", "external": {"url": poster}}

    r = requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=payload)
    print(r.status_code)
    if r.status_code != 200:
        print(json.dumps(r.json(), indent=2))

if __name__ == "__main__":
    tmdb_id = resolve_tmdb_id(sys.argv[1])
    details = get_movie_details(tmdb_id)
    add_movie(details)
    print(f"Added: {details['title']} ({(details.get('release_date') or '????')[:4]})")