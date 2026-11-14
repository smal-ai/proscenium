import io
from dataclasses import replace

import pytest
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.artifacts import Artifacts
from proscenium.sources.common import sha


def image_bytes(mode="RGB", *, format="PNG", exif=None):
    output = io.BytesIO()
    image = Image.new(mode, (8, 4), 0)
    kwargs = {"exif": exif} if exif else {}
    image.save(output, format=format, **kwargs)
    return output.getvalue()


@pytest.mark.parametrize(
    "format,media_type", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_upload_exact_bytes_retry_conflict_range_and_head(
    client, owner_headers, source_services, format, media_type
):
    data = image_bytes(format=format)
    url = "/api/v1/projects/project-test/uploads/upload-test"
    headers = {**owner_headers, "Content-Type": media_type, "Idempotency-Key": "upload-test"}
    response = client.put(url, content=data, headers=headers)
    assert response.status_code == 201, response.text
    artifact = response.json()
    assert client.get("/api/v1/projects/project-test/artifacts/upload-test").json() == artifact
    assert artifact["sha256"] == sha(data)
    assert artifact["dimensions"] == {"width": 8, "height": 4}
    assert client.put(url, content=data, headers=headers).status_code == 200
    assert client.put(url, content=data + b"different bytes", headers=headers).status_code == 409
    content = "/api/v1/projects/project-test/artifacts/upload-test/content"
    assert client.get(content).content == data
    head = client.head(content)
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(data))
    for header, expected in [
        ("bytes=1-7", data[1:8]),
        ("bytes=-5", data[-5:]),
        ("bytes=5-", data[5:]),
        ("bytes=0-999999", data),
    ]:
        response = client.get(content, headers={"Range": header})
        assert response.status_code == 206
        assert response.content == expected
        assert response.headers["content-length"] == str(len(expected))
        assert client.head(content, headers={"Range": header}).status_code == 206
    for header in ["bytes=999999-", "bytes=9-2", "bytes=-0", "bytes=0-1,4-5", "nonsense"]:
        response = client.get(content, headers={"Range": header})
        assert response.status_code == 416
        assert response.headers["content-range"] == f"bytes */{len(data)}"
    path = source_services.extension("artifacts").ready_path("project-test", "upload-test")
    path.chmod(0o600)
    path.write_bytes(b"corrupt")
    assert client.get(content).status_code == 422


def test_upload_limits_from_shared_settings_and_invalid_images(source_services):
    settings = replace(source_services.settings, upload_max_bytes=10, upload_max_pixels=1)
    original = source_services.settings
    source_services.settings = settings
    limited = Artifacts(source_services)
    assert limited.limits.bytes == 10 and limited.limits.pixels == 1
    with pytest.raises(DomainError) as error:
        limited.decode(image_bytes(), "image/png")
    assert error.value.code == "upload_too_large"
    source_services.settings = replace(settings, upload_max_bytes=100000)
    with pytest.raises(DomainError) as error:
        Artifacts(source_services).decode(image_bytes(), "image/png")
    assert error.value.code == "image_dimensions_exceeded"
    source_services.settings = original
    for data, media_type in [
        (b"<svg/>", "image/svg+xml"),
        (b"not an image", "image/png"),
        (image_bytes(), "image/jpeg"),
    ]:
        with pytest.raises(DomainError):
            Artifacts(source_services).decode(data, media_type)


def test_exif_derivative_preserves_original_with_explicit_transform(source_services):
    exif = Image.Exif()
    exif[274] = 6
    original = image_bytes(format="JPEG", exif=exif)
    artifacts = source_services.extension("artifacts")
    result, _ = artifacts.upload(
        Caller("owner", "owner-test"), "project-test", "upload-oriented", original, "image/jpeg"
    )
    assert result["dimensions"] == {"width": 4, "height": 8}
    assert result["derived_from"][0]["kind"] == "orientation"
    original_id = result["derived_from"][0]["input_artifact_ids"][0]
    assert artifacts.ready_path("project-test", original_id).read_bytes() == original
    assert artifacts.ready_path("project-test", "upload-oriented").read_bytes() != original


def test_masks_staging_scope_and_registration_replay(source_services, source_caller, root):
    artifacts = source_services.extension("artifacts")
    directory = root / ".proscenium/tool-output/staging"
    directory.mkdir(parents=True)
    data = image_bytes("L")
    (directory / "mask.png").write_bytes(data)
    body = dict(
        schema_version="1.0",
        operation_id="register-mask",
        project_id="project-test",
        artifact_id="mask-test",
        staging_path=".proscenium/tool-output/staging/mask.png",
        kind="mask",
        purpose="mask",
        sha256=sha(data),
        media_type="image/png",
    )
    mask, repeated = artifacts.register(source_caller, body)
    assert not repeated and mask["kind"] == "mask"
    assert (
        artifacts.validate_mask(
            "project-test", "mask-test", dimensions={"width": 8, "height": 4}, semantics="binary"
        )
        == mask
    )
    with pytest.raises(DomainError):
        artifacts.validate_mask(
            "project-test", "mask-test", dimensions={"width": 4, "height": 8}, semantics="soft"
        )
    (directory / "mask.png").unlink()
    source_services.gate.close("project-test")
    assert artifacts.register(source_caller, body)[1]
    source_services.gate.resume("project-test", reconciliation_evidence="offline")
    for mode in ["RGB", "RGBA", "P", "1", "I;16"]:
        with pytest.raises(DomainError):
            artifacts.decode(image_bytes(mode), "image/png", mask=True)
    (root / "assets/raw.png").write_bytes(data)
    with pytest.raises(DomainError) as error:
        artifacts.register(
            source_caller,
            {**body, "operation_id": "register-unsafe", "staging_path": "assets/raw.png"},
        )
    assert error.value.code == "scope_denied"
    (directory / "escape.png").symlink_to(root / "assets/raw.png")
    with pytest.raises(DomainError):
        artifacts.register(
            source_caller,
            {
                **body,
                "operation_id": "register-symlink",
                "staging_path": ".proscenium/tool-output/staging/escape.png",
            },
        )


def test_retention_is_explicit_owner_choice(source_services, root):
    artifacts = source_services.extension("artifacts")
    record = artifacts.promote(
        "project-test",
        "retained-blob",
        b"owner private source",
        kind="source_blob",
        purpose="metadata",
        media_type="application/octet-stream",
    )
    with source_services.registry.project_store("project-test").transaction() as tx:
        artifacts.put(tx, record)
    with pytest.raises(DomainError):
        artifacts.retain(
            Caller("tool", "tool-test"),
            "project-test",
            "retained-blob",
            reason="licence text is not authority",
        )
    artifacts.retain(
        Caller("owner", "owner-test"),
        "project-test",
        "retained-blob",
        reason="Keep this source out of Git",
    )
    from proscenium.sources import closure_digest

    manifest = dict(
        entry_path="assets/local.bin",
        files=[
            dict(
                destination="assets/local.bin",
                origin=dict(kind="retained", artifact_id="retained-blob", sha256=record["sha256"]),
            )
        ],
        dependencies=[],
        required_runtime=dict(application="fixture", version="1", features=[]),
        digest="0" * 64,
    )
    manifest["digest"] = closure_digest(manifest)
    materialised = source_services.extension("sources").resolve("project-test", manifest)
    assert (materialised / "assets/local.bin").read_bytes() == b"owner private source"


def test_soft_mask_bytes_and_coverage_round_trip_without_alpha(source_services):
    output = io.BytesIO()
    image = Image.frombytes("L", (3, 1), bytes([0, 127, 255]))
    image.save(output, format="PNG")
    data = output.getvalue()
    artifacts = source_services.extension("artifacts")
    mask = artifacts.promote(
        "project-test", "mask-soft", data, kind="mask", purpose="mask", media_type="image/png"
    )
    with source_services.registry.project_store("project-test").transaction() as tx:
        artifacts.put(tx, mask)
    assert artifacts.ready_path("project-test", "mask-soft").read_bytes() == data
    assert (
        artifacts.validate_mask(
            "project-test", "mask-soft", dimensions={"width": 3, "height": 1}, semantics="soft"
        )
        == mask
    )
    with pytest.raises(DomainError):
        artifacts.validate_mask(
            "project-test", "mask-soft", dimensions={"width": 3, "height": 1}, semantics="binary"
        )
