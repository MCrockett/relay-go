"""Writing-session usage per feature, stage and model (writer-usage D2, D7, D10, R9)."""
import time

from . import holds, transcripts

GAP_CAP_S = 300
PERIODS = (7, 30)


def _stage(window, at):
    """The stage of the window's latest state commit at `at`; before its first commit, the first stage."""
    stage = window["stages"][0][1] if window["stages"] else None
    for when, name in window["stages"]:
        if when <= at:
            stage = name
    return stage


def _latest_commit(window, at):
    return max((when for when, _ in window["stages"] if when <= at), default=float("-inf"))


def attribute(windows, turns_by_session):
    """Each turn to at most one feature (D10): the open window whose branch the turn was on, else the one with the
    newest state commit, then repo and slug. Minutes are the gap since the session's previous turn, capped (D7)."""
    out = []
    for (provider, session), turns in turns_by_session.items():
        mine = [w for w in windows if w["provider"] == provider and w["session"] == session]
        previous = None
        for t in sorted(turns, key=lambda t: t["at"]):
            gap = 0 if previous is None else min(GAP_CAP_S, max(0, t["at"] - previous))
            previous = t["at"]
            open_ = [w for w in mine if w["start"] <= t["at"] and (w["end"] is None or t["at"] < w["end"])]
            match = [w for w in open_ if t.get("branch") and w["branch"] == t["branch"]]
            pick = sorted(match or open_, key=lambda w: (-_latest_commit(w, t["at"]), w["repo"], w["slug"]))
            w = pick[0] if pick else None
            out.append({**t, "provider": provider, "session": session, "minutes": gap / 60,
                        "repo": w and w["repo"], "slug": w and w["slug"], "stage": w and _stage(w, t["at"])})
    return out


def _new(**extra):
    return {"sessions": set(), "turns": 0, "input": 0, "cached": 0, "output": 0, "minutes": 0, **extra}


def _add(group, t):
    group["turns"] += 1
    group["sessions"].add((t["provider"], t["session"]))
    for k in ("input", "cached", "output", "minutes"):
        group[k] += t[k]


def _rows(groups):
    rows = []
    for name, g in sorted(groups.items()):
        row = {"name": name, "sessions": len(g["sessions"]), "turns": g["turns"], "input": g["input"],
               "cached": g["cached"], "output": g["output"], "minutes": g["minutes"],
               "cached_share": g["cached"] / g["input"] * 100 if g["input"] else 0}
        row.update({k: g[k] for k in ("feature", "model") if k in g})
        rows.append(row)
    return rows


def totals(attributed, since_s, now):
    features, models, both, loose = {}, {}, {}, {}
    for t in attributed:
        if t["at"] < now - since_s:
            continue
        if t["slug"] is None:
            _add(loose.setdefault(t["provider"], _new()), t)
            continue
        feature, model = f"{t['repo']} · {t['slug']} · {t['stage']} ({t['provider']})", f"{t['provider']}:{t['model']}"
        _add(features.setdefault(feature, _new()), t)
        _add(models.setdefault(model, _new()), t)
        _add(both.setdefault(f"{feature} · {model}", _new(feature=feature, model=model)), t)
    unattributed = {p: {k: v for k, v in g.items() if k != "sessions"} for p, g in loose.items()}
    return {"features": _rows(features), "models": _rows(models), "feature_models": _rows(both),
            "unattributed": unattributed}


def summary(checkouts, now=None, periods=PERIODS):
    """Totals for each period (in days, keyed by str(days)), plus the sessions whose logs could not be read and
    notes about history and cache."""
    now = time.time() if now is None else now
    held = holds.windows(checkouts)
    sessions = {(w["provider"], w["session"]) for w in held["windows"] if w["provider"] in ("claude", "codex")}
    read = transcripts.read_sessions(sessions)
    attributed = attribute(held["windows"], read["turns"])
    out = {str(days): totals(attributed, days * 86400, now) for days in periods}
    out["unreadable"] = [{"provider": p, "session": s, "reason": r} for (p, s), r in sorted(read["unreadable"].items())]
    out["notes"] = held["flags"] + read["notes"]
    return out
