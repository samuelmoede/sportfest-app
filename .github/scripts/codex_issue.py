"""Trusted issue gate and data-only publisher for the Codex workflow."""

import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import urllib.error
import urllib.request


MAX_BYTES = 1_000_000
MAX_FILES = 50


def authorized(event, actor, owner):
    """Only a fresh, explicit owner mention in a real issue may spend API quota."""
    issue = event.get("issue", {})
    sender = event.get("sender", {})
    if (actor != owner or sender.get("login") != owner
            or sender.get("type") != "User" or issue.get("pull_request")
            or issue.get("state") != "open"):
        return False
    if "comment" in event:
        if event.get("action") != "created" or event["comment"].get("user", {}).get("login") != owner:
            return False
        text = event["comment"].get("body", "")
    else:
        if event.get("action") != "opened" or issue.get("user", {}).get("login") != owner:
            return False
        text = issue.get("title", "") + "\n" + issue.get("body", "")
    return re.search(r"(?<![\w@])@codex\b(?![-\w])", text, re.I) is not None


def allowed_path(path):
    if not isinstance(path, str) or not path or "\\" in path or ":" in path:
        return False
    parts = path.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return False
    if PurePosixPath(path).name.lower() in ("agents.md", "claude.md"):
        return False
    if path.lower().endswith((".db", ".sqlite", ".sqlite3", ".pem", ".key")):
        return False
    return (parts[0] in ("app", "tests", "docs") and len(parts) > 1
            or path in ("README.md", "CHANGELOG.md", "VERSION", "requirements.txt", "requirements-dev.txt"))


def validate(bundle):
    if not isinstance(bundle, dict) or not re.fullmatch(r"[0-9a-f]{40}", bundle.get("base", "")):
        raise ValueError("Invalid develop base SHA")
    files = bundle.get("files")
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError("Too many files")
    seen = set()
    size = 0
    for item in files:
        path = item.get("path")
        if not allowed_path(path) or path.casefold() in seen:
            raise ValueError(f"Forbidden or duplicate path: {path!r}")
        seen.add(path.casefold())
        if item.get("mode") not in ("100644", "100755"):
            raise ValueError("Symlinks and submodules are not allowed")
        content = item.get("content")
        if content is not None:
            if not isinstance(content, str) or "\x00" in content:
                raise ValueError("Only UTF-8 text changes are allowed")
            size += len(content.encode("utf-8"))
    if size > MAX_BYTES:
        raise ValueError("Change exceeds size limit")
    return bundle


def git(*args):
    return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", *args])


def export(base, target):
    # Include new files and compare against the original checkout even if Codex committed.
    git("add", "--all")
    paths = git("diff", "--cached", "--name-only", "-z", base).decode().split("\0")
    files = []
    for path in filter(None, paths):
        if not allowed_path(path):
            raise ValueError(f"Codex changed a protected path: {path}")
        entry = git("ls-files", "--stage", "--", path).decode().strip()
        if not entry:
            files.append({"path": path, "mode": "100644", "content": None})
        else:
            mode, sha, _ = entry.split(maxsplit=2)
            files.append({"path": path, "mode": mode,
                          "content": git("cat-file", "blob", sha).decode("utf-8")})
    Path(target).write_text(json.dumps(validate({"base": base, "files": files})), encoding="utf-8")


def api(method, path, data=None):
    request = urllib.request.Request(
        "https://api.github.com/repos/" + os.environ["GITHUB_REPOSITORY"] + "/" + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def publish(filename, event):
    if not authorized(event, os.environ["GITHUB_ACTOR"], os.environ["REPO_OWNER"]):
        raise ValueError("Unauthorized event")
    source = Path(filename)
    if source.is_symlink() or source.stat().st_size > MAX_BYTES * 7:
        raise ValueError("Invalid artifact")
    bundle = validate(json.loads(source.read_text(encoding="utf-8")))
    if not bundle["files"]:
        print("No changes; no PR created.")
        return
    base = bundle["base"]
    comparison = api("GET", f"compare/{base}...develop")
    if comparison["status"] not in ("identical", "ahead"):
        raise ValueError("The proposed base is not an ancestor of develop")
    parent = api("GET", "git/commits/" + base)
    tree = []
    for item in bundle["files"]:
        row = {"path": item["path"], "mode": item["mode"], "type": "blob"}
        if item["content"] is None:
            row["sha"] = None
        else:
            row["content"] = item["content"]
        tree.append(row)
    new_tree = api("POST", "git/trees", {"base_tree": parent["tree"]["sha"], "tree": tree})
    issue = int(event["issue"]["number"])
    run = int(os.environ["GITHUB_RUN_ID"])
    attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
    branch = f"codex/issue-{issue}-run-{run}-{attempt}"
    commit = api("POST", "git/commits", {"message": f"feat: Codex-Vorschlag fuer Issue #{issue}",
                 "tree": new_tree["sha"], "parents": [base]})
    # Create only: no protected ref updates, force pushes, merges or deployment APIs.
    api("POST", "git/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
    repository = os.environ["GITHUB_REPOSITORY"]
    pr = api("POST", "pulls", {"title": f"Codex: Umsetzung zu Issue #{issue}",
             "head": branch, "base": "develop", "draft": True,
             "body": f"Refs #{issue}\n\nAutomatischer Umsetzungsvorschlag auf Basis von develop. "
             "Bitte Aenderungen und Tests menschlich pruefen; Codex merged nicht.\n\n"
             f"[Ausfuehrung und Testprotokoll](https://github.com/{repository}/actions/runs/{run})\n\n"
             "Vor der Uebergabe lief `python -m pytest tests -v` erfolgreich im Agent-Job. "
             "Massgeblich bleiben die unabhaengigen PR-Pflichtchecks `test` und `docker`."})
    if pr["base"]["ref"] != "develop":
        raise ValueError("Unexpected PR base")
    print(pr["html_url"])
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(f"Entwurf erstellt: {pr['html_url']}\n")


def main():
    command = sys.argv[1]
    if command == "export":
        export(sys.argv[2], sys.argv[3])
        return
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    if command == "gate":
        allowed = authorized(event, os.environ["GITHUB_ACTOR"], os.environ["REPO_OWNER"])
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"allowed={'true' if allowed else 'false'}\n")
    elif command == "publish":
        publish(sys.argv[2], event)
    else:
        raise ValueError("Unknown command")


if __name__ == "__main__":
    main()
