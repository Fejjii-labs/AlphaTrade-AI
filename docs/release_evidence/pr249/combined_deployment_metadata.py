"""Reconcile installed revisions after feature integration; emit safe metadata only."""
import concurrent.futures
import json
import os
import urllib.error
import urllib.request

team = "team_LNgcEGBkqntUnTrktNjFzh7a"
project = "prj_y7vFqwbFvdpHvf2nMaDkKQauBhZQ"
targets = [
    ("api", "https://api.render.com/v1/services/srv-d8fvbcd7vvec739mc060/deploys?limit=1", "RENDER_API_KEY"),
    ("worker", "https://api.render.com/v1/services/srv-daqej7o473hc73fsc1k0/deploys?limit=1", "RENDER_API_KEY"),
    ("frontend", "https://api.vercel.com/v6/deployments?projectId=" + project + "&teamId=" + team + "&limit=15", "VERCEL_TOKEN"),
]

def inspect(target):
    label, url, variable = target
    try:
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + os.environ[variable]})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        if label == "frontend":
            rows = []
            for row in data.get("deployments", []):
                meta = row.get("meta", {})
                if meta.get("githubCommitRef") in ["main", "codex/interactive-voice-monitoring", "codex/frontend-refinement-pr249"]:
                    rows.append({"id": row.get("uid"), "url": row.get("url"), "state": row.get("state"),
                                 "target": row.get("target"), "created": row.get("created"),
                                 "sha": meta.get("githubCommitSha"), "branch": meta.get("githubCommitRef")})
        else:
            rows = [{"id": x["deploy"].get("id"), "status": x["deploy"].get("status"),
                     "created_at": x["deploy"].get("createdAt"),
                     "sha": x["deploy"].get("commit", {}).get("id")} for x in data]
        return {"target": label, "http_status": 200, "deployments": rows}
    except urllib.error.HTTPError as error:
        return {"target": label, "http_status": error.code}
    except Exception as error:
        return {"target": label, "error_type": type(error).__name__}

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    for result in pool.map(inspect, targets):
        print(json.dumps(result))
