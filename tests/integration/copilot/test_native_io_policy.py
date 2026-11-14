"""Read/write data is not a shell command. Real path, native-stage and owner guards remain."""

import asyncio
import copy
import json
from pathlib import Path

import pytest
from proscenium.copilot.state import key, save
from test_delivery import PROJECT, context, submit
from test_delivery import runtime as runtime

CAPTURE = json.loads(Path("docs/evidence/m11-final/permission-blocker.json").read_text())["request"]


async def setup(runtime, request):
    context(runtime)
    submit(runtime)
    await runtime.send(PROJECT, "request-one")
    session = runtime.native.session
    session.event(
        "tool.execution_start",
        dict(
            toolCallId=request["toolCallId"],
            toolName="rg" if request["kind"] == "read" else "create_file",
            arguments={"paths": request.get("path", request.get("fileName"))},
        ),
        parent=session.last_event,
    )
    session.event(
        "permission.requested", dict(requestId="io-permission", permissionRequest=request)
    )
    return session


def io_request(runtime, kind, name="ordinary.txt"):
    root = Path(runtime.services.registry.get(PROJECT)["path"])
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "read":
        path.write_text(
            "Source prose: pip install, sudo, git push, upload, rm -rf, .env, credentials. Not executed.\n"
        )
    request = dict(
        kind=kind,
        toolCallId="file-io",
        intention="Inspect or edit source explaining install, push, sudo and credentials",
    )
    if kind == "read":
        request["path"] = str(path)
    else:
        request.update(
            fileName=str(path),
            diff="Example source containing sudo install and git push",
            canOfferSessionApproval=False,
            newFileContents="Documentation: curl --upload-file example, npm install, rm -rf. Ordinary source, not a command.",
        )
    return request


def test_actual_m11_rg_read_capture_uses_automatic_policy(runtime):
    async def run():
        request = copy.deepcopy(CAPTURE)
        request["path"] = io_request(
            runtime, "read", "unity/M11/Assets/Proscenium/tool-install.json"
        )["path"]
        request["intention"] = "Search in file: " + request["path"]
        session = await setup(runtime, request)
        result = await asyncio.wait_for(
            runtime.permission(PROJECT, request, {"session_id": session.session_id}), 0.1
        )
        assert result.kind == "approve-once"
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert tx.all("decisions") == []
            assert tx.all("copilot.permissions")[0]["authority"] == "automatic_policy"
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["read", "write"])
@pytest.mark.parametrize("word", ["install", "push", "sudo", "purchase", "upgrade", "scp"])
def test_ordinary_file_names_prose_and_source_contents_are_not_consequential_commands(
    runtime, kind, word
):
    async def run():
        request = io_request(runtime, kind, f"docs/tool-{word}.txt")
        session = await setup(runtime, request)
        result = await asyncio.wait_for(
            runtime.permission(PROJECT, request, {"session_id": session.session_id}), 0.1
        )
        assert result.kind == "approve-once"
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["read", "write"])
@pytest.mark.parametrize(
    "bad",
    [
        "secret",
        "runtime_root",
        "runtime_db",
        "outside",
        "symlink_outside",
        "symlink_secret",
        "lexical_secret_symlink",
        "resolved_override",
        "traversal",
    ],
)
def test_native_io_never_uses_resolved_or_text_fields_to_bypass_path_guards(
    runtime, tmp_path, kind, bad
):
    async def run():
        request = io_request(runtime, kind)
        root = Path(runtime.services.registry.get(PROJECT)["path"])
        outside = tmp_path / "outside.txt"
        outside.write_text("Outside test fixture")
        secret = root / ".env"
        secret.write_text("NOT_A_CREDENTIAL=fixture\n")
        if bad == "secret":
            target = secret
        elif bad == "runtime_root":
            target = root / ".proscenium"
        elif bad == "runtime_db":
            target = root / ".proscenium/state.sqlite3"
        elif bad == "outside":
            target = outside
        elif bad == "symlink_outside":
            target = root / "outside-link"
            target.symlink_to(outside)
        elif bad == "symlink_secret":
            target = root / "secret-link"
            target.symlink_to(secret)
        elif bad == "lexical_secret_symlink":
            directory = root / ".ssh"
            directory.mkdir()
            target = directory / "source-link"
            target.symlink_to(root / "ordinary.txt")
        elif bad == "resolved_override":
            target = secret
            request["resolvedPath"] = str(root / "ordinary.txt")
        else:
            target = root / "docs/../ordinary.txt"
        request["path" if kind == "read" else "fileName"] = str(target)
        session = await setup(runtime, request)
        result = await asyncio.wait_for(
            runtime.permission(PROJECT, request, {"session_id": session.session_id}), 0.1
        )
        assert result.kind == "reject"
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert tx.all("decisions") == []
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad", ["session", "tool", "work", "epoch", "missing_path", "file_edits", "resolved_mismatch"]
)
def test_io_requires_supported_sdk_shape_and_original_live_invocation(runtime, bad):
    async def run():
        request = io_request(runtime, "write" if bad == "file_edits" else "read")
        if bad == "missing_path":
            request["path"] = ""
        if bad == "file_edits":
            request["fileEdits"] = [{"after": {"path": "../outside.txt", "content": "not allowed"}}]
        if bad == "resolved_mismatch":
            other = io_request(runtime, "read", "other.txt")
            request["resolvedPath"] = other["path"]
        session = await setup(runtime, request)
        sid = session.session_id
        if bad == "session":
            sid = "different-session"
        if bad == "tool":
            request = {**request, "toolCallId": "different-tool"}
        if bad in {"work", "epoch"}:
            with runtime.services.registry.project_store(PROJECT).transaction() as tx:
                inv = tx.require("invocations", key(sid, "tool", request["toolCallId"]))
                save(
                    tx,
                    "invocations",
                    inv["invocation_id"],
                    {
                        **inv,
                        ("work_id" if bad == "work" else "owner_epoch"): "wrong-original-binding",
                    },
                )
        result = await asyncio.wait_for(
            runtime.permission(PROJECT, request, {"session_id": sid}), 0.1
        )
        assert result.kind == "reject"
        await runtime.close()

    asyncio.run(run())


def test_explicit_native_managed_approval_flag_still_waits_for_owner(runtime):
    async def run():
        request = io_request(runtime, "read")
        request["managedApprovalRequired"] = True
        session = await setup(runtime, request)
        task = asyncio.create_task(
            runtime.permission(PROJECT, request, {"session_id": session.session_id})
        )
        await asyncio.sleep(0.01)
        assert not task.done()
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert tx.all("decisions")[0]["response"] is None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        "captured_missing_file",
        "deleted_parent",
        "missing_outside",
        "missing_secret",
        "broken_outside_symlink",
        "missing_wrong_binding",
    ],
)
def test_missing_file_is_not_missing_authority_but_all_path_and_binding_guards_still_apply(
    runtime, tmp_path, case
):
    async def run():
        root = Path(runtime.services.registry.get(PROJECT)["path"])
        request = dict(
            kind="read",
            toolCallId="call_pKoAp26w293LCPhPJ3iV6uz1",
            intention="Read file: assets/e04/revision-measurements.json",
            path=str(root / "assets/e04/revision-measurements.json"),
        )
        if case == "deleted_parent":
            parent = root / "removed-parent"
            parent.mkdir()
            parent.rmdir()
            request["path"] = str(parent / "missing.txt")
        elif case == "missing_outside":
            request["path"] = str(tmp_path / "outside-parent/missing.txt")
        elif case == "missing_secret":
            request["path"] = str(root / ".env.not-created")
        elif case == "broken_outside_symlink":
            link = root / "broken"
            link.symlink_to(tmp_path / "outside-missing-parent", target_is_directory=True)
            request["path"] = str(link / "missing.txt")
        session = await setup(runtime, request)
        if case == "missing_wrong_binding":
            with runtime.services.registry.project_store(PROJECT).transaction() as tx:
                original = tx.require(
                    "invocations", key(session.session_id, "tool", request["toolCallId"])
                )
                save(
                    tx,
                    "invocations",
                    original["invocation_id"],
                    {**original, "owner_epoch": "not-the-owner"},
                )
        result = await asyncio.wait_for(
            runtime.permission(PROJECT, request, {"session_id": session.session_id}), 0.1
        )
        assert (result.kind == "approve-once") == (
            case in {"captured_missing_file", "deleted_parent"}
        )
        assert not Path(request["path"]).exists()
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert tx.all("decisions") == []
        await runtime.close()

    asyncio.run(run())
