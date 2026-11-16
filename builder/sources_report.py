#!/usr/bin/env python3
"""Writes what the build used, from reports/<cc>.json and the Mobility Database catalog:

  sources/used.json     every timetable feed a line of the database comes from (provider, licence, link, lines)
  sources/FEEDS.md      the same as a table, for the attribution
  sources/review.json   feeds near an extract that were NOT used and why (no licence / a licence not on the allow-list / needs a key),
                        with the licence link: a person reads it and adds the licence to feeds.ALLOW or CHECKED

  python builder/sources_report.py [--root .] [--catalog work/feeds_v2.csv]
"""
import argparse
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ebrdb import feeds  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(HERE.parent))
    ap.add_argument("--catalog", default=str(HERE.parent / "work" / "feeds_v2.csv"))
    a = ap.parse_args(argv)
    root = pathlib.Path(a.root)
    catalog = {f["id"]: f for f in feeds.load_catalog(a.catalog)}
    used, review_ids = {}, collections.defaultdict(set)
    for rp in sorted((root / "reports").glob("*.json")):
        cc = rp.stem
        r = json.loads(rp.read_text(encoding="utf-8"))
        for u in r.get("used_feeds", []):
            e = used.setdefault(u["id"], {**u, "patterns": 0, "countries": []})
            e["patterns"] += u["patterns"]
            e["countries"].append(cc)
        for fid in r.get("feeds_review", []):
            review_ids[fid].add(cc)
    out = [dict(sorted(v.items())) for v in sorted(used.values(), key=lambda v: (v["provider"], v["id"]))]
    (root / "sources" / "used.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    lines = ["# Timetable feeds used", "",
             "Lines with times come from these feeds (open licences only). Each line says which feed it comes from in its `src` field.", "",
             "| Provider | Licence | Feed | Countries |", "|---|---|---|---|"]
    for v in out:
        lic = f"[{v['license']}]({v['license_url']})" if v.get("license_url", "").startswith("http") else v["license"]
        lines.append(f"| {v['provider'].replace('|', '/')} | {lic} | {v['id']} | {', '.join(sorted(set(v['countries'])))} |")
    (root / "sources" / "FEEDS.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    review = []
    for fid, ccs in sorted(review_ids.items()):
        f = catalog.get(fid)
        if f is None:
            continue
        why = "needs an API key or login" if f["auth"] else ("no licence given" if not f["license_url"] else "licence not on the allow-list")
        review.append({"id": fid, "provider": f["provider"], "why": why, "license_url": f["license_url"], "countries": sorted(ccs)})
    (root / "sources" / "review.json").write_text(json.dumps(review, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    n_lic = collections.Counter(r["license_url"] for r in review if r["license_url"])
    print(f"used feeds: {len(out)}; for review: {len(review)} (with a licence link: {sum(n_lic.values())})")
    for url, n in n_lic.most_common(12):
        print(f"  {n:3d} {url[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
