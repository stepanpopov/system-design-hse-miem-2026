#!/usr/bin/env python3
import json
import os
import urllib.request
import urllib.error

GRAFANA = "http://localhost:3000"
AUTH = "admin:admin"
DASH_DIR = os.path.join(os.path.dirname(__file__), "grafana_dashboards")

import base64
_basic = base64.b64encode(AUTH.encode()).decode()


def api(method, path, payload=None):
    url = GRAFANA + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Basic " + _basic)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def ensure_prometheus_ds():
    # уже есть?
    status, body = api("GET", "/api/datasources")
    for ds in body if isinstance(body, list) else []:
        if ds.get("type") == "prometheus":
            return ds["uid"]
    payload = {
        "name": "Prometheus",
        "type": "prometheus",
        "access": "proxy",
        "url": "http://prometheus:9090",
        "isDefault": True,
    }
    status, body = api("POST", "/api/datasources", payload)
    uid = body.get("datasource", {}).get("uid") or body.get("uid")
    print(f"Created prometheus datasource: status={status} uid={uid}")
    return uid


def remap_datasources(obj, new_uid):
    if isinstance(obj, dict):
        if obj.get("uid") and obj.get("uid") != "grafana" and (
            obj.get("type") in (None, "prometheus")
        ) and set(obj.keys()) <= {"type", "uid"}:
            obj["uid"] = new_uid
            obj.setdefault("type", "prometheus")
        for v in obj.values():
            remap_datasources(v, new_uid)
    elif isinstance(obj, list):
        for v in obj:
            remap_datasources(v, new_uid)


def import_dashboard(path, new_uid):
    model = json.load(open(path))
    remap_datasources(model, new_uid)
    model["id"] = None
    payload = {"dashboard": model, "overwrite": True, "folderId": 0}
    status, body = api("POST", "/api/dashboards/db", payload)
    print(f"  {os.path.basename(path):14s} -> status={status} "
          f"title='{model.get('title')}' url={body.get('url')}")


def main():
    print("Ensuring Prometheus datasource...")
    uid = ensure_prometheus_ds()
    print("Importing dashboards...")
    for f in ("first.json", "second.json", "third.json"):
        import_dashboard(os.path.join(DASH_DIR, f), uid)


if __name__ == "__main__":
    main()
