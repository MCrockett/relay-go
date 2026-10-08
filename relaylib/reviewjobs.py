"""Owner-requested reviews of a build, or of an approved spec or plan (stage-rereview): reviewer choices, one job
per feature, run in an isolated worktree."""
import argparse
import dataclasses
import fcntl
import hashlib
import json
import os
import tempfile
import threading
import time

from . import availability, config, gitops, machine, notify, owneractions, runner, state, usage
from .config import relay_home
from .errors import RelayError

LOADED_AT = time.time()  # the dashboard shows jobs since its server started (R11)
_JOBS, _JOBS_LOCK = {}, threading.Lock()  # running background jobs: thread -> Job
_STOPPING = threading.Event()  # set by shutdown: no new jobs, and nothing publishes after it
STOPPED = "the dashboard stopped during the review; nothing was published"


def spec_id(spec):
    return f"{spec.provider}:{spec.model}" + (f"@{spec.effort}" if spec.effort else "")


def choices(cfg):
    """Every distinct [review.prefer] entry, all authors, in table order, with availability now."""
    out, seen = [], set()
    for entries in ((cfg.get("review") or {}).get("prefer") or {}).values():
        for entry in entries:
            spec = config.parse_model_spec(entry)
            key = spec_id(spec)
            if key in seen:
                continue
            seen.add(key)
            out.append(option(spec, cfg["limits"]))
    return out


def option(spec, limits):
    """One reviewer as the dashboard shows it: id, parts, and whether its provider can review now."""
    blocked, why = availability.blocked(spec.provider, limits)
    return {"id": spec_id(spec), "provider": spec.provider, "model": spec.model, "effort": spec.effort,
            "available": not blocked, "reason": why if blocked else ""}


def default(cfg, st, options=None, stage="build"):
    """(choice id or None, label, note): for build, the pending confirmation's reviewer; else the first choice
    for the stage's author."""
    options = choices(cfg) if options is None else options
    note, pending = "", st.get("confirm_with") if stage == "build" else None
    if pending:  # stored as provider:model, without effort
        match = next((o for o in options if f"{o['provider']}:{o['model']}" == pending), None)
        if match:
            return match["id"], "confirms the fallback GO", ""
        note = f"the pending reviewer {pending} is no longer configured"
    first = next(iter(config.review_preferences(cfg, (st.get("authors") or {}).get(stage))), None)
    if first and spec_id(first) in {o["id"] for o in options}:
        return spec_id(first), "default", note
    return None, "", note


class Busy(owneractions.Conflict):
    """A review is already running for this feature."""


def _paths(repo, slug):
    """One lock and one status file per feature: origin URL and slug, so worktrees and clones share it."""
    key = hashlib.sha1(f"{gitops.origin_url(repo)}\n{slug}".encode()).hexdigest()
    folder = os.path.join(relay_home(), "review-jobs")
    return os.path.join(folder, key + ".lock"), os.path.join(folder, key + ".json")


class JobLock:
    """An exclusive flock held for a job's whole run, plus its status file, across processes."""

    def __init__(self, repo, slug):
        self.lock_path, self.info_path = _paths(repo, slug)
        self.fd, self.info = None, {}

    def acquire(self):
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise Busy("a review is already running for this feature")
        self.fd = fd

    def write(self, **fields):
        self.info.update(fields)
        usage.atomic_write(self.info_path, json.dumps(self.info) + "\n")

    def release(self):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def _held(lock_path):
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def status(repo, slug):
    """The feature's latest job since this process started, or None. A job recorded as running whose lock
    is free ended without a result, whenever it started."""
    lock_path, info_path = _paths(repo, slug)
    try:
        with open(info_path) as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None
    if info.get("state") == "running" and not _held(lock_path):
        return dict(info, state="failed", message="ended without a result")
    if info.get("started_at", 0) < LOADED_AT and info.get("state") != "running":
        return None
    return info


class WorkCtx:
    """What review_current needs, on a detached worktree. save commits and never pushes: the job
    publishes by lease once the verdict is in."""

    def __init__(self, root, slug, cfg):
        self.env, self.root, self.cfg, self.slug = dict(os.environ), root, cfg, slug
        self.path = state.state_path(root, slug)
        self.st = state.read_state(self.path)
        self.dir = state.feature_dir(root, slug)
        self.saved = False

    def save(self, message, push="best"):
        state.write_state(self.path, self.st)
        gitops.commit_paths_under(self.root, f"{state.RELAY_DIR}/{self.slug}", message)
        self.saved = True


@dataclasses.dataclass
class Job:
    repo: str
    slug: str
    seen: dict
    spec: config.ModelSpec
    label: str
    relayed_by: str | None
    lock: JobLock
    cfg: dict
    id: str
    stage: str = "build"
    stopped: bool = False

    def run(self):
        """Run and publish; always records the outcome and frees the feature."""
        try:
            message, ok = _run(self), True
        except Exception as e:  # anything that goes wrong publishes nothing; the owner sees why
            message, ok = str(e) or e.__class__.__name__, False
            if self.stopped or _STOPPING.is_set():
                message = STOPPED
        try:
            self.lock.write(state="done" if ok else "failed", message=message, ended_at=time.time())
        finally:
            self.lock.release()
        return {"ok": ok, "message": message}


STAGES = ("spec", "plan", "build")


def prepare(repo, slug, seen, reviewer=None, relayed_by=None, stage="build"):
    """Lock the feature, check the request against what the owner saw and the configured reviewers,
    and return a Job ready to run. Raises Busy, owneractions.Conflict or RelayError."""
    if stage not in STAGES:
        raise RelayError(f"cannot review {stage!r}: choose spec, plan or build")
    if _STOPPING.is_set():
        raise RelayError("the dashboard is stopping; request the review again after it restarts")
    lock = JobLock(repo, slug)
    lock.acquire()
    try:
        with owneractions.action_lock():
            fresh, st = owneractions._validate(repo, slug, "review" if stage == "build" else f"review-{stage}", seen)
        if stage == "build":
            if fresh.get("github_error"):
                raise RelayError(f"GitHub is unknown: {fresh['github_error']}")
            if gitops.pr_info(repo, st["pr"]).get("state") != "OPEN":
                raise RelayError("the PR is not open")
        elif st.get("pr"):  # a merged PR can leave any published status behind; ask GitHub whatever it says
            try:
                merged_pr = gitops.pr_info(repo, st["pr"]).get("state") == "MERGED"
            except (RelayError, OSError) as e:
                raise RelayError(f"GitHub is unknown: {e}")
            if merged_pr:
                raise RelayError("the feature is merged; nothing to re-review")
        cfg = config.load(repo)
        options = choices(cfg)
        if not options:
            raise RelayError('no reviewers configured: run relay roles set review.<author> "provider:model, ..."')
        default_id, default_label, _ = default(cfg, st, options, stage)
        chosen = reviewer or default_id
        if not chosen:
            raise RelayError("this feature has no default reviewer; pick one with --reviewer")
        option = next((o for o in options if o["id"] == chosen), None)
        if option is None:
            raise RelayError(f"{chosen} is not a configured reviewer; choose one of: "
                             + ", ".join(o["id"] for o in options))
        if not option["available"]:
            raise RelayError(f"{chosen} is not available: {option['reason']}")
        label = default_label if chosen == default_id else ""
        started = time.time()
        job_id = f"{os.path.basename(lock.lock_path)[:12]}-{int(started * 1000)}"
        job = Job(repo, slug, fresh, config.parse_model_spec(chosen), label, relayed_by, lock, cfg, job_id, stage)
        lock.write(id=job_id, reviewer=chosen, started_at=started, pid=os.getpid(), state="running", message="")
        return job
    except BaseException:
        lock.release()
        raise


def _run(job):
    from .commands import record_owner_action, review_current  # commands imports this module for the CLI
    who = spec_id(job.spec)
    with tempfile.TemporaryDirectory(prefix="relay-review-") as temp:
        work = os.path.join(temp, "work")
        gitops.git(job.repo, "worktree", "add", "--detach", work, job.seen["commit"])
        try:
            c = WorkCtx(work, job.slug, job.cfg)
            st = c.st
            if job.stage == "build":
                record_owner_action(st, f"owner requested a build review from {who}"
                                    + (f" ({job.label})" if job.label else ""), job.relayed_by)
                chain = ["build"]
            else:
                record_owner_action(st, f"owner requested a {job.stage} re-review from {who}"
                                    + (f" ({job.label})" if job.label else ""), job.relayed_by)
                # The plan's GO was given on the spec: a spec re-review carries on to it (D4). Read before
                # mark_for_refresh clears it.
                chain = [job.stage] + (["plan"] if job.stage == "spec" and st["verdicts"].get("plan") == "GO" else [])
            spec = job.spec
            if not spec.effort:
                release = job.stage == "build" and gitops.pr_info(work, st["pr"]).get("baseRefName") == "main"
                spec = dataclasses.replace(spec, effort=config.review_effort(job.cfg, True, release))
            done = []
            for stage in chain:
                machine.mark_for_refresh(c.st, stage)
                c.st["confirming"] = True  # an independent fresh review, exactly like a fallback confirmation
                review_current(c, argparse.Namespace(), candidates=[spec], discard_errors=True, announce=False)
                if not c.saved:
                    raise RelayError("the review did not record a verdict")
                done.append(stage)
                if c.st["verdicts"].get(stage) != "GO":
                    break
            branch = job.seen["branch"]
            if not owneractions.ACTION_LOCK.acquire(timeout=60):  # held only to publish, never while reviewing
                raise RelayError("another owner action kept the lock; nothing was published. Request again.")
            try:
                if job.stopped or _STOPPING.is_set():  # a review that finished during shutdown publishes nothing
                    raise RelayError(STOPPED)
                push = gitops.network_git(work, "push", f"--force-with-lease=refs/heads/{branch}:{job.seen['commit']}",
                                          "origin", f"HEAD:refs/heads/{branch}")
            finally:
                owneractions.ACTION_LOCK.release()
            if push.returncode:
                raise RelayError("the branch moved during the review; nothing was published. Request again.")
            last, before = done[-1], (f"{done[0]} GO, then " if len(done) > 1 else "")
            if job.stage != "build" and c.st["verdicts"].get(last) == "GO":
                message = f"{job.slug}: {' and '.join(done)} GO from {who}; back at {c.st['stage']}, {c.st['status']}."
            elif c.st["status"] == "ready-to-merge":
                message = f"{job.slug}: build GO from {who}; ready to merge."
            elif c.st["status"] == "waiting-owner":
                message = f"{job.slug}: {before}{last} NO-GO from {who}; the review loop stopped and waits on you."
            else:
                message = f"{job.slug}: {before}{last} NO-GO from {who}; back to the author."
            notify.send(job.cfg, f"relay: {c.st['repo']} review done", message)  # only once it is published
            return message
        finally:
            gitops.git(job.repo, "worktree", "remove", "--force", work, check=False)


def start(job, on_done=None):
    """Run a prepared job on a background thread (the dashboard). Registration and thread start are one
    step under _JOBS_LOCK, the lock shutdown takes to set _STOPPING, so shutdown never misses a started job
    and never waits on a thread that has not started."""
    def body():
        try:
            job.run()
        finally:
            with _JOBS_LOCK:
                _JOBS.pop(threading.current_thread(), None)
            if on_done:
                on_done()
    thread = threading.Thread(target=body, name=f"relay-review-{job.slug}", daemon=True)
    with _JOBS_LOCK:
        if _STOPPING.is_set():
            job.lock.release()
            raise RelayError("the dashboard is stopping; request the review again after it restarts")
        _JOBS[thread] = job
        thread.start()
    return thread


def shutdown():
    """The dashboard is stopping: stop running reviewers, then wait for their jobs to record the failure
    and remove their worktrees. _STOPPING is set while holding the owner action lock, the lock every job
    holds for its final check and push: a push already under way finishes first, and every later job sees
    the flag under that lock and publishes nothing. There is no time limit: owner actions and pushes hold
    the lock for seconds, and the reviewers are stopped, so every job ends; returning earlier could leave a
    publication or a worktree behind."""
    with owneractions.ACTION_LOCK:
        with _JOBS_LOCK:
            _STOPPING.set()
            running = dict(_JOBS)
    for job in running.values():
        job.stopped = True
    runner.stop_all()  # also refuses reviewers that try to start from now on
    for thread in running:
        thread.join()
