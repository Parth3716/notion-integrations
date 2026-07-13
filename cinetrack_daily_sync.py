import sys
import os, time, requests
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(line_buffering=True)
TMDB_API_KEY = os.environ["TMDB_API_KEY"]
NOTION_TOKEN = os.environ["NOTION_API_KEY"]
LIFEOS_PAGE_ID = os.environ["NOTION_LIFEOS_PAGE_ID"]
MOVIES_DS = os.environ["NOTION_MOVIES_DATA_SOURCE_ID"]
SHOWS_DS = os.environ["NOTION_SHOWS_DATA_SOURCE_ID"]
EPISODES_DS = os.environ["NOTION_EPISODES_DATA_SOURCE_ID"]
EPISODES_ARCHIVE_DS = os.environ["NOTION_EPISODES_ARCHIVE_DATA_SOURCE_ID"]

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

def query_all(ds_id, filter_obj=None):
    results, body = [], ({"filter": filter_obj} if filter_obj else {})
    while True:
        r = notion.post(f"https://api.notion.com/v1/data_sources/{ds_id}/query", json=body)
        r.raise_for_status()
        data = r.json()
        results.extend(data["results"])
        if not data.get("has_more"):
            break
        body["start_cursor"] = data["next_cursor"]
    return results

def get_select(p, name):
    s = p["properties"].get(name, {}).get("select")
    return s["name"] if s else None

def get_formula_text(p, name):
    return p["properties"].get(name, {}).get("formula", {}).get("string", "") or ""

def get_number(p, name):
    return p["properties"].get(name, {}).get("number") or 0

def get_checkbox(p, name):
    return p["properties"].get(name, {}).get("checkbox", False)

def hr_min(total_minutes):
    if total_minutes < 60:
        return f"{total_minutes}m"
    total_hours = total_minutes // 60
    minutes = total_minutes % 60
    if total_hours < 24:
        return f"{total_hours}h {minutes}m"
    days = total_hours // 24
    hours = total_hours % 24
    if days < 365:
        return f"{days}d {hours}h"
    years = days // 365
    remaining_days = days % 365
    return f"{years}y {remaining_days}d"

def get_date(p, name):
    d = p["properties"].get(name, {}).get("date")
    return d["start"] if d else None

def get_title(p, name):
    t = p["properties"].get(name, {}).get("title", [])
    return "".join(x.get("plain_text", "") for x in t)

def get_file_url(p, name):
    files = p["properties"].get(name, {}).get("files", [])
    if not files:
        return None
    f = files[0]
    return f.get("external", {}).get("url") or f.get("file", {}).get("url")

# --- Part 1: refresh metadata, add only-current-season missing episodes ---

def add_missing_episodes_current_season(show_page_id, tmdb_id, current_season):
    existing = query_all(EPISODES_DS, filter_obj={"and": [
        {"property": "Show", "relation": {"contains": show_page_id}},
        {"property": "Season Number", "number": {"equals": current_season}},
    ]})
    existing_keys = {get_number(e, "Episode Number") for e in existing}

    season_data = tmdb_get(f"/tv/{tmdb_id}/season/{current_season}")
    for ep in season_data["episodes"]:
        if ep["episode_number"] in existing_keys:
            continue
        code = f"S{current_season:02d}E{ep['episode_number']:02d}"
        title = f"{code} - {ep.get('name', '')}".strip(" -")
        props = {
            "Episode": {"title": [{"text": {"content": title}}]},
            "Show": {"relation": [{"id": show_page_id}]},
            "Season Number": {"number": current_season},
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
        print(f"  + new episode: {code}")
        time.sleep(0.34)

def refresh_show(show_page):
    tmdb_id = show_page["properties"]["TMDB ID"]["number"]
    if not tmdb_id:
        return
    d = tmdb_get(f"/tv/{tmdb_id}")

    props = {"Total Episodes": {"number": d.get("number_of_episodes")}}
    if d.get("status"):
        props["Air Status"] = {"select": {"name": d["status"]}}

    next_air = d.get("next_episode_to_air")
    props["Upcoming Air Date"] = {"date": {"start": next_air["air_date"]}} if next_air and next_air.get("air_date") else {"date": None}
    if next_air:
        if next_air.get("name"):
            props["Upcoming Episode Name"] = {"rich_text": [{"text": {"content": next_air["name"]}}]}
        props["Upcoming Episode Code"] = {"rich_text": [{"text": {"content": f"S{next_air['season_number']:02d}E{next_air['episode_number']:02d}"}}]}
        if next_air.get("still_path"):
            props["Upcoming Episode Still URL"] = {"files": [{"name": "poster.jpg", "type": "external", "external": {"url": f"https://image.tmdb.org/t/p/w500{next_air['still_path']}"}}]}

    notion.patch(f"https://api.notion.com/v1/pages/{show_page['id']}", json={"properties": props})

    last_ep = d.get("last_episode_to_air")
    if last_ep:
        add_missing_episodes_current_season(show_page["id"], tmdb_id, last_ep["season_number"])

# --- Part 2: recompute stats ---

def compute_stats():
    movies = query_all(MOVIES_DS)
    shows = query_all(SHOWS_DS)
    episodes = query_all(EPISODES_DS)

    movies_watched = [m for m in movies if get_select(m, "Status") == "Watched"]
    episodes_watched_live = [e for e in episodes if get_checkbox(e, "Watched")]
    archived_shows = [s for s in shows if get_checkbox(s, "Is Archived")]
    # archived shows no longer resolve Status="Watched" live (their episode
    # relation is gone), so they're counted here instead of double-counted
    shows_watched_live = [s for s in shows if get_formula_text(s, "Status") in ("Watching", "Watched")]

    total_episodes_watched = len(episodes_watched_live) + sum(get_number(s, "Archived Episodes Watched") for s in archived_shows)
    total_tv_minutes = sum(get_number(e, "Runtime (min)") for e in episodes_watched_live) + sum(get_number(s, "Archived TV Minutes") for s in archived_shows)

    return {
        "Episodes Watched": str(total_episodes_watched),
        "TV Time": hr_min(total_tv_minutes),
        "Shows Watched": str(len(shows_watched_live) + len(archived_shows)),
        "Movie Time": hr_min(sum(get_number(m, "Runtime (min)") for m in movies_watched)),
        "Movies Watched": str(len(movies_watched)),
    }

def find_callouts(block_id, depth=0, max_depth=8):
    found = []
    r = notion.get(f"https://api.notion.com/v1/blocks/{block_id}/children")
    r.raise_for_status()
    for b in r.json()["results"]:
        if b["type"] == "callout":
            text = "".join(t.get("plain_text", "") for t in b["callout"].get("rich_text", []))
            found.append((b["id"], text))
        if b.get("has_children") and depth < max_depth:
            found.extend(find_callouts(b["id"], depth + 1, max_depth))
    return found

def update_stats_callouts():
    stats = compute_stats()
    for block_id, text in find_callouts(LIFEOS_PAGE_ID):
        label = text.split(":")[0].strip()
        if label in stats:
            new_text = f"{label}: {stats[label]}"
            notion.patch(f"https://api.notion.com/v1/blocks/{block_id}", json={"callout": {"rich_text": [{"text": {"content": new_text}}]}})
            print(label, "->", stats[label])

def archive_finished_shows():
    """Move episodes of shows that are Ended/Canceled and fully watched into
    Episodes Archive, freeze their stats on the Show page, then trash the
    live episode rows. Resumable: if this crashes partway through a show,
    the next run only sees the episodes still left (trashed ones drop out
    of the query), so it won't duplicate — but if a crash happens mid-show,
    double check that show's frozen totals afterward before trusting them."""
    shows = query_all(SHOWS_DS)
    for show in shows:
        if get_checkbox(show, "Is Archived"):
            continue
        if get_select(show, "Air Status") not in ("Ended", "Canceled"):
            continue
        if get_formula_text(show, "Status") != "Watched":
            continue

        show_id = show["id"]
        name = "".join(t["plain_text"] for t in show["properties"]["Name"]["title"])

        episodes = query_all(EPISODES_DS, filter_obj={
            "property": "Show", "relation": {"contains": show_id}
        })
        if not episodes:
            continue

        print(f"Archiving: {name} ({len(episodes)} episodes)")

        watched_eps = [e for e in episodes if get_checkbox(e, "Watched")]
        episodes_watched = len(watched_eps)
        tv_minutes = sum(get_number(e, "Runtime (min)") for e in watched_eps)
        watched_dates = [get_date(e, "Watched Date") for e in watched_eps if get_date(e, "Watched Date")]
        last_watched = max(watched_dates) if watched_dates else None

        for ep in episodes:
            props = {
                "Episode": {"title": [{"text": {"content": get_title(ep, "Episode")}}]},
                "Show": {"relation": [{"id": show_id}]},
                "Season Number": {"number": get_number(ep, "Season Number")},
                "Episode Number": {"number": get_number(ep, "Episode Number")},
                "Watched": {"checkbox": get_checkbox(ep, "Watched")},
            }
            if get_date(ep, "Watched Date"):
                props["Watched Date"] = {"date": {"start": get_date(ep, "Watched Date")}}
            if get_date(ep, "Air Date"):
                props["Air Date"] = {"date": {"start": get_date(ep, "Air Date")}}
            if get_number(ep, "Runtime (min)"):
                props["Runtime (min)"] = {"number": get_number(ep, "Runtime (min)")}
            still = get_file_url(ep, "Still URL")
            if still:
                props["Still URL"] = {"files": [{"name": "still.jpg", "type": "external", "external": {"url": still}}]}

            payload = {"parent": {"type": "data_source_id", "data_source_id": EPISODES_ARCHIVE_DS}, "properties": props}
            notion.post("https://api.notion.com/v1/pages", json=payload).raise_for_status()
            time.sleep(0.34)

            notion.patch(f"https://api.notion.com/v1/pages/{ep['id']}", json={"in_trash": True})
            time.sleep(0.34)

        freeze = {
            "Is Archived": {"checkbox": True},
            "Archived Episodes Watched": {"number": episodes_watched},
            "Archived TV Minutes": {"number": tv_minutes},
        }
        if last_watched:
            freeze["Archived Last Watched"] = {"date": {"start": last_watched}}
        notion.patch(f"https://api.notion.com/v1/pages/{show_id}", json={"properties": freeze})
        print(f"  done: {episodes_watched} watched, {tv_minutes} min, last watched {last_watched}")

if __name__ == "__main__":
    print("Refreshing returning shows + adding new episodes...")
    for show in query_all(SHOWS_DS, filter_obj={"property": "Air Status", "select": {"equals": "Returning Series"}}):
        name = "".join(t["plain_text"] for t in show["properties"]["Name"]["title"])
        print(f"- {name}")
        refresh_show(show)

    print("\nArchiving finished shows...")
    archive_finished_shows()
    print("\nUpdating stats...")
    update_stats_callouts()
    print("\nDone:", datetime.now(timezone.utc).isoformat())
