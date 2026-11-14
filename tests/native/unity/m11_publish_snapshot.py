"""Private before/after copies and immutable-source guards for the retained M11 work."""

import hashlib
import json
import sqlite3
import subprocess

PROJECT_ID = "project-6efca2f5-8ccd-49bb-bea1-f552ef065224"
WORK_ID = "work-4a7e644e78514109b4a5232316e3aacc"
REQUEST_ID = "1a914aa8-2bbc-461a-a87d-1bb7431de02c"
SESSION_ID = "0cbee1a2-b5ed-4ab8-962b-ccf1d9f8a454"


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def records(database, namespace):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM records WHERE namespace=? ORDER BY id", (namespace,)
            )
        ]


def snapshot(original, control, label):
    project = original / "project"
    target = control / ("private-" + label)
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    copied = {}
    for name, path in {
        "project.sqlite3": project / ".proscenium/state.sqlite3",
        "registry.sqlite3": original / "machine/registry.sqlite3",
    }.items():
        with sqlite3.connect(path) as source, sqlite3.connect(target / name) as destination:
            source.backup(destination)
        (target / name).chmod(0o600)
        copied[name] = sha(target / name)
    native = original / "machine/copilot-runtime/home/session-state" / SESSION_ID / "events.jsonl"
    if native.exists():
        import shutil

        shutil.copyfile(native, target / "native-events.jsonl")
        copied["native-events.jsonl"] = sha(target / "native-events.jsonl")
    sources = [
        p
        for folder in ("Assets", "Packages", "ProjectSettings")
        for p in (project / "unity/M11" / folder).rglob("*")
        if p.is_file()
    ]
    sources += [project / "unity/M11/M11-operator-playmode.json"]
    data = dict(
        copies=copied,
        source_files={p.relative_to(project).as_posix(): sha(p) for p in sources},
        old_reports={p.name: sha(p) for p in original.iterdir() if p.is_file()},
        git_index=sha(project / ".git/index") if (project / ".git/index").is_file() else None,
        head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip(),
        refs=subprocess.check_output(
            ["git", "for-each-ref", "--format=%(refname) %(objectname)"], cwd=project, text=True
        ).splitlines(),
        jobs=records(project / ".proscenium/state.sqlite3", "jobs"),
        versions=records(project / ".proscenium/state.sqlite3", "versions"),
        work=records(project / ".proscenium/state.sqlite3", "work"),
        requests=records(project / ".proscenium/state.sqlite3", "requests"),
        invocations=records(project / ".proscenium/state.sqlite3", "invocations"),
    )
    (control / (label + ".json")).write_text(json.dumps(data, indent=2))
    return data
