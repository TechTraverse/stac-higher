# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Summarise a soak JSONL (Q4 + Q8). Usage: uv run analyse.py soak-c13.jsonl"""
import collections
import datetime as dt
import json
import statistics as st
import sys

ev = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
ts = lambda e: dt.datetime.fromisoformat(e["ts"])
c = collections.Counter(e["event"] for e in ev)
start, end = ts(ev[0]), ts(ev[-1])
app = [e for e in ev if e["event"] == "append" and not e.get("seed")]
out = dict(span_h=round((end - start).total_seconds() / 3600, 2), events=dict(c), commits=len(app))


def q(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 3) if xs else None


# commit latency by 6-hour bucket (flat = no growth)
buckets = collections.defaultdict(list)
for e in app:
    buckets[int((ts(e) - start).total_seconds() // (6 * 3600))].append(e)
out["by_6h"] = [dict(bucket=f"{6*k}-{6*k+6}h", n=len(v),
                     total_p50=q([e["total_s"] for e in v], .5), total_p95=q([e["total_s"] for e in v], .95),
                     commit_p50=q([e["commit_s"] for e in v], .5), commit_p95=q([e["commit_s"] for e in v], .95),
                     trim_p50=q([e["trim_s"] for e in v], .5), parse_p50=q([e["parse_s"] for e in v], .5),
                     manifests_max=max(e.get("manifests", 0) for e in v), repo_bytes_max=max(e.get("repo_bytes", 0) for e in v),
                     objects_last=v[-1].get("objects"), rss_mb_max=max(e["rss_mb"] for e in v), t_len_last=v[-1]["t_len"])
                for k, v in sorted(buckets.items())]
gcs = [e for e in ev if e["event"] == "gc"]
out["gc"] = dict(runs=len(gcs), expire_s_max=max((e["expire_s"] for e in gcs), default=None),
                 gc_s_max=max((e["gc_s"] for e in gcs), default=None),
                 after_first=gcs[0].get("objects") if gcs else None, after_last=gcs[-1].get("objects") if gcs else None,
                 bytes_after_first=gcs[0].get("repo_bytes") if gcs else None, bytes_after_last=gcs[-1].get("repo_bytes") if gcs else None,
                 last_summary=gcs[-1].get("gc_summary") if gcs else None)
rc = [e for e in ev if e["event"] == "read_check"]
out["read_checks"] = dict(n=len(rc), all_ok=all(e["ok"] for e in rc), read_s_max=max((e["read_s"] for e in rc), default=None))
# Q8: arrival behaviour
pl = [e["publish_lag_s"] for e in app if "publish_lag_s" in e]
dl = [e["detect_lag_s"] for e in app if "detect_lag_s" in e]
out["q8"] = dict(
    publish_lag_s=dict(p50=q(pl, .5), p95=q(pl, .95), max=max(pl, default=None)),
    detect_lag_s=dict(p50=q(dl, .5), p95=q(dl, .95), max=max(dl, default=None)),
    late=[dict(key=e["key"].rsplit("/", 1)[1][:60], behind_s=e.get("behind_s"), via=e.get("via", "poll")) for e in ev if e["event"] == "late"],
    gaps=[dict(after=e["after"], before=e["before"], missing=e["missing_steps"]) for e in ev if e["event"] == "gap"],
    missing_steps_total=sum(e["missing_steps"] for e in ev if e["event"] == "gap"),
    duplicates=c.get("duplicate", 0))
out["errors"] = [dict(ts=e["ts"], where=e.get("where"), error=e.get("error", "")[:200]) for e in ev if e["event"] in ("error", "fatal")]
print(json.dumps(out, indent=1, default=str))
