from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.catalogue import install, projects_router, router
from proscenium.contracts import canonical
from proscenium.projects.service import Projects
from proscenium.sources.common import git


@pytest.fixture
def source_services(services):
    services.settings = replace(
        services.settings,
        profiles=(
            {
                "profile_id": "offline",
                "name": "Synthetic offline profile",
                "lead_model_id": None,
                "worker_model_ids": [],
                "scope_status": "project_scoped",
            },
        ),
    )
    root = Path(services.registry.get("project-test")["path"])
    Projects.bootstrap(root, "Synthetic W02 fixture")
    config = dict(
        schema_version="1.0",
        project_id="project-test",
        repository_id="repo-test",
        name="Synthetic W02 fixture",
        paths={p: p for p in ("brief", "assets", "scenes", "storyboards", "sequences")},
    )
    (root / "project.json").write_bytes(canonical(config))
    git(root, "config", "user.name", "W02 Offline Tests")
    git(root, "config", "user.email", "w02@example.invalid")
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "invocations",
            "invocation-test",
            {"work_id": "work-test", "owner_epoch": services.ownership.epoch, "state": "running"},
        )
        tx.put(
            "project_identity",
            "identity",
            {"project_id": "project-test", "repository_id": "repo-test"},
        )
    with services.registry.store.transaction() as tx:
        tx.put(
            "private",
            "settings:project-test",
            dict(generation_attempt_limit=3, default_profile_id=None, state_revision=0),
        )
    services.gate.resume(
        "project-test", reconciliation_evidence="synthetic bounded offline fixture"
    )
    install(services)
    return services


@pytest.fixture
def source_caller(source_services):
    return Caller(
        "tool",
        "tool-test",
        "project-test",
        "work-test",
        source_services.ownership.epoch,
        operations=("checkpoint_sources", "publishOutputs", "registerToolArtifact"),
        invocation_id="invocation-test",
    )


@pytest.fixture
def root(source_services):
    return Path(source_services.registry.get("project-test")["path"])


@pytest.fixture
def client(source_services):
    with TestClient(
        create_app(
            source_services.settings, services=source_services, routers=[projects_router, router]
        ),
        base_url=source_services.settings.origin,
    ) as client:
        yield client
