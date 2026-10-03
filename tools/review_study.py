#!/usr/bin/env python3.11
"""Repeat or extend a reviewer model study: replay settled reviews with other models and score them by hand.

  review_study.py case <repo> <slug> [--stage build] [--round 1]      print a case from a settled review
  review_study.py import <repo> <slug> --case-id ID --out DIR [...]   the original review as a baseline result
  review_study.py run --cases cases.json --models "a,b" --out DIR     every model reviews every case
  review_study.py sheet --cases cases.json --results DIR              scoring sheet to judge by hand
  review_study.py report --cases cases.json --results DIR --scores scores.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from relaylib import study  # noqa: E402


def main(argv):
    p = argparse.ArgumentParser(prog="review_study.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("case", "import"):
        s = sub.add_parser(name)
        s.add_argument("repo")
        s.add_argument("slug")
        s.add_argument("--stage", default="build")
        s.add_argument("--round", type=int, default=1)
        if name == "import":
            s.add_argument("--case-id", required=True)
            s.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--cases", required=True)
    r.add_argument("--models", required=True, help="comma-separated provider:model@effort")
    r.add_argument("--out", required=True)
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--timeout-min", type=float, default=20)
    for name in ("sheet", "report"):
        s = sub.add_parser(name)
        s.add_argument("--cases", required=True)
        s.add_argument("--results", required=True)
        if name == "report":
            s.add_argument("--scores", required=True)
    a = p.parse_args(argv)
    if a.cmd == "case":
        print(json.dumps(study.case_from_review(a.repo, a.slug, a.stage, a.round), indent=2))
    elif a.cmd == "import":
        res = study.result_from_review(a.repo, a.slug, a.stage, a.round, a.case_id)
        path = study.result_path(a.out, a.case_id, res["label"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(res, open(path, "w"), indent=2)
        print(path)
    elif a.cmd == "run":
        cases = json.load(open(a.cases))["cases"]
        for res in study.run(cases, [m.strip() for m in a.models.split(",")], a.out, a.workers, a.timeout_min * 60):
            print(f"{res['case']:40} {res['label']:40} {res['verdict']} {len(res['blocking'])} blockers "
                  f"{res['duration_s']}s {res['error'][:60]}")
    else:
        cases = json.load(open(a.cases))["cases"]
        results = study.load_results(a.results)
        if a.cmd == "sheet":
            print(study.sheet(cases, results))
        else:
            print(study.report(cases, results, json.load(open(a.scores))))


if __name__ == "__main__":
    main(sys.argv[1:])
