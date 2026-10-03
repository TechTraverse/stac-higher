# /// script
# requires-python = ">=3.12"
# dependencies = ["procrastinate==3.9.0", "psycopg[binary]>=3.2,<4"]
# ///
"""Q7a: does a Procrastinate `lock` serialize jobs across two worker PROCESSES, and
does `queueing_lock` coalesce a burst? Runs only against the throwaway z1-pg.

    uv run q7_lock.py schema       # apply the procrastinate schema to z1-pg
    uv run q7_lock.py defer        # defer the test jobs
    uv run q7_lock.py worker NAME  # run a worker (start two in parallel)
    uv run q7_lock.py report       # check for overlap
"""
import json
import logging
import os
import sys
import time

import procrastinate

DSN = "postgresql://postgres:z1@localhost:15432/postgres"
LOG = os.path.expanduser("~/stac-higher-z1-spike/q7_lock.jsonl")
app = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=DSN))


@app.task(name="cube_append_sim", queue="cube")
def cube_append_sim(sink: str, n: int):
    start = time.time()
    time.sleep(2.0)
    with open(LOG, "a") as f:
        f.write(json.dumps(dict(sink=sink, n=n, pid=os.getpid(), worker=os.environ.get("W"), start=start, end=time.time())) + "\n")


def main():
    cmd = sys.argv[1]
    if cmd == "schema":
        with app.open():
            app.schema_manager.apply_schema()
        print("schema applied")
    elif cmd == "defer":
        open(LOG, "w").close()
        with app.open():
            # 4 jobs on sink A and 4 on sink B, each with lock=cube:<sink>; no queueing_lock here
            for n in range(4):
                for sink in ("A", "B"):
                    cube_append_sim.configure(lock=f"cube:{sink}").defer(sink=sink, n=n)
            # queueing_lock burst: only one may wait per sink
            accepted, rejected = 0, 0
            for n in range(5):
                try:
                    cube_append_sim.configure(lock="cube:C", queueing_lock="cube:C").defer(sink="C", n=n)
                    accepted += 1
                except procrastinate.exceptions.AlreadyEnqueued:
                    rejected += 1
            print(json.dumps(dict(queueing_lock_burst=dict(accepted=accepted, rejected=rejected))))
    elif cmd == "worker":
        os.environ["W"] = sys.argv[2]
        import asyncio

        async def run():
            async with app.open_async():
                try:
                    await asyncio.wait_for(app.run_worker_async(queues=["cube"], concurrency=4, wait=True,
                                                                delete_jobs="never"), timeout=25)
                except TimeoutError:
                    pass
        asyncio.run(run())
    elif cmd == "report":
        rows = [json.loads(l) for l in open(LOG)]
        out = {}
        for sink in sorted({r["sink"] for r in rows}):
            rs = sorted((r for r in rows if r["sink"] == sink), key=lambda r: r["start"])
            overlaps = sum(1 for a, b in zip(rs, rs[1:]) if b["start"] < a["end"])
            out[sink] = dict(jobs=len(rs), overlaps=overlaps, workers=sorted({r["worker"] for r in rs}),
                             pids=len({r["pid"] for r in rs}), span_s=round(rs[-1]["end"] - rs[0]["start"], 2))
        # cross-sink parallelism: did A and B ever run at the same time?
        a = [r for r in rows if r["sink"] == "A"]; b = [r for r in rows if r["sink"] == "B"]
        out["A_B_parallel_pairs"] = sum(1 for x in a for y in b if x["start"] < y["end"] and y["start"] < x["end"])
        print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
