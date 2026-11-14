from proscenium.sources import closure_digest
from proscenium.sources.common import git, sha


def commit(root, name="scenes/source.txt", content=b"authored v1\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    git(root, "add", "--", name, ".gitattributes")
    git(root, "commit", "-m", "Synthetic test source")
    return git(root, "rev-parse", "HEAD").decode().strip()


def closure(commit_id, name="scenes/source.txt", content=b"authored v1\n", dependencies=None):
    result = dict(
        entry_path=name,
        files=[
            dict(
                destination=name,
                origin=dict(
                    kind="git",
                    repository_id="repo-test",
                    commit=commit_id,
                    path=name,
                    sha256=sha(content),
                ),
            )
        ],
        dependencies=dependencies or [],
        required_runtime=dict(application="fixture", version="1", features=[]),
        digest="0" * 64,
    )
    result["digest"] = closure_digest(result)
    return result


def publication(
    commit_id,
    source,
    *,
    publication_id="publication-test",
    item_id="item-test",
    version_id="version-test",
    head=None,
    **metadata,
):
    return dict(
        schema_version="1.0",
        publication_id=publication_id,
        project_id="project-test",
        items=[
            dict(
                item_id=item_id,
                version_id=version_id,
                kind="scene",
                name="Published name",
                expected_current_version_id=head,
                source_commit=commit_id,
                source=source,
                artifact_ids=[],
                preview_requests=[],
                **metadata,
            )
        ],
    )


def ready_image(services, identity="image-end"):
    import io

    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(output, format="PNG")
    artifacts = services.extension("artifacts")
    artifact = artifacts.promote(
        "project-test",
        identity,
        output.getvalue(),
        kind="image",
        purpose="concept",
        media_type="image/png",
    )
    with services.registry.project_store("project-test").transaction() as tx:
        artifacts.put(tx, artifact)
    return artifact
