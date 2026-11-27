"""Offline regression tests for model-facing schemas and tool validation errors."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from copilot.tools import ToolInvocation
from jsonschema import Draft202012Validator
from proscenium.api.errors import DomainError
from proscenium.catalogue.validation import (
    safe_schema_guidance,
    schema_issues,
    schema_issues_for_schema,
)
from proscenium.contracts import SCHEMA
from proscenium.copilot.tool_schemas import inline_tool_schema
from proscenium.copilot.tools import (
    TOOLS,
    Tools,
    _read_schema,
    read_arguments,
    tool_failure,
    tool_schema,
)
from proscenium.copilot.workflows import WORKFLOW_TOOLS, _parameter_schema


@pytest.fixture
def tools(monkeypatch):
    # No project state, provider or native process is needed for argument validation.
    services = SimpleNamespace(settings=SimpleNamespace(execution_profiles={}), extensions={})
    adapter = Tools(services, None, "project-one")
    monkeypatch.setattr(adapter, "caller", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(adapter, "_outcome", lambda *args, **kwargs: None)
    return adapter


def failure(tools, name, arguments):
    invocation = ToolInvocation(
        session_id="session-one", tool_call_id="call-one", tool_name=name, arguments=arguments
    )
    response = asyncio.run(tools.invoke(name, invocation))
    assert response.result_type == "failure"
    return response.text_result_for_llm, json.loads(response.text_result_for_llm)


def assert_issue(result, code, path, message):
    assert (
        dict(phase="schema", code=code, path=path, message=message) in result["details"]["issues"]
    )


def test_all_tool_definitions_are_self_contained(tools):
    def assert_inline(node):
        if isinstance(node, dict):
            assert not node.keys() & {"$ref", "$defs", "const", "$comment"}
            for value in node.values():
                assert_inline(value)
        elif isinstance(node, list):
            for value in node:
                assert_inline(value)

    definitions = {tool.name: tool.parameters for tool in tools.definitions()}
    assert len(definitions) == 26
    for schema in definitions.values():
        assert_inline(schema)
    preview = definitions["request_preview"]["properties"]
    spec = preview["spec"]["properties"]
    assert spec["colour_management"]["properties"]["gamma"]["exclusiveMinimum"] == 0
    assert spec["fps"]["properties"]["numerator"]["type"] == "integer"
    assert preview["schema_version"] == {"type": "string", "enum": ["1.0"]}
    review = definitions["request_vlm_review"]["properties"]
    assert set(review["targets"]["items"]["properties"]["origin"]["enum"]) == {
        "concept",
        "reference",
        "blender_render",
        "sequence_render",
        "proxy_capture",
    }
    unity = definitions["run_unity_workflow"]["properties"]
    assert unity["version"]["properties"]["version_id"] == SCHEMA["$defs"]["Id"]
    assembly = definitions["assemble_sequence"]["properties"]
    assert assembly["execution_profile_id"] == {
        **SCHEMA["$defs"]["Id"],
        "description": "Omit to use the host's configured assembly profile.",
    }
    assert SCHEMA["$defs"]["PreviewRequest"]["properties"]["schema_version"] == {"const": "1.0"}


@pytest.mark.parametrize(
    "value,expected_type",
    [
        (True, "boolean"),
        (1, "integer"),
        (None, "null"),
        ("one", "string"),
        (1.5, "number"),
        ([1], "array"),
        ({"const": 1}, "object"),
    ],
)
def test_const_inlining_preserves_type_siblings_and_literals(value, expected_type):
    assert inline_tool_schema({"const": value, "description": "Fixed value"}) == {
        "type": expected_type,
        "enum": [value],
        "description": "Fixed value",
    }


def test_inline_refs_preserve_constraints_and_reject_cycles(monkeypatch):
    root = {"$ref": "#/$defs/Id", "pattern": "^example", "description": "Example"}
    assert inline_tool_schema(root) == {
        "allOf": [SCHEMA["$defs"]["Id"], {"pattern": "^example"}],
        "description": "Example",
    }
    monkeypatch.setitem(SCHEMA["$defs"], "TestA", {"$ref": "#/$defs/TestB"})
    monkeypatch.setitem(SCHEMA["$defs"], "TestB", {"items": {"$ref": "#/$defs/TestA"}})
    with pytest.raises(ValueError, match="Cycle in tool schema references"):
        inline_tool_schema({"$ref": "#/$defs/TestA"})


def test_ref_sibling_additional_properties_remains_independent():
    raw = {"$ref": "#/$defs/VersionRef", "additionalProperties": False}
    baseline = Draft202012Validator({**raw, "$defs": SCHEMA["$defs"]})
    projected = Draft202012Validator(inline_tool_schema(raw))
    instance = {"item_id": "item-one", "version_id": "version-one"}
    assert not baseline.is_valid(instance)
    assert not projected.is_valid(instance)


def test_ref_sibling_unevaluated_properties_sees_enclosing_all_of():
    raw = {"$ref": "#/$defs/VersionRef", "unevaluatedProperties": False}
    baseline = Draft202012Validator({**raw, "$defs": SCHEMA["$defs"]})
    schema = inline_tool_schema(raw)
    assert schema["unevaluatedProperties"] is False
    assert len(schema["allOf"]) == 1
    projected = Draft202012Validator(schema)
    valid = {"item_id": "item-one", "version_id": "version-one"}
    invalid = {**valid, "extra": True}
    for instance, expected in ((valid, True), (invalid, False)):
        assert baseline.is_valid(instance) == projected.is_valid(instance) == expected


def test_ref_sibling_unevaluated_items_sees_enclosing_all_of(monkeypatch):
    monkeypatch.setitem(SCHEMA["$defs"], "TestTuple", {"prefixItems": [{"type": "string"}]})
    raw = {"$ref": "#/$defs/TestTuple", "minItems": 1, "unevaluatedItems": False}
    baseline = Draft202012Validator({**raw, "$defs": SCHEMA["$defs"]})
    schema = inline_tool_schema(raw)
    assert schema == {
        "allOf": [SCHEMA["$defs"]["TestTuple"], {"minItems": 1}],
        "unevaluatedItems": False,
    }
    projected = Draft202012Validator(schema)
    for instance, expected in ((["value"], True), (["value", "extra"], False), ([], False)):
        assert baseline.is_valid(instance) == projected.is_valid(instance) == expected


def test_all_tool_validators_agree_with_unprojected_sources(tools, preview):
    version = preview["version"]
    wire = dict(schema_version="1.0", project_id="project-one")
    operation = {**wire, "operation_id": "operation-one"}
    job = {**operation, "job_id": "job-one"}
    prep = dict(schema_version="1.0", operation_id="operation-one", prompt="A scene", seed="1")
    target = dict(
        origin="concept",
        artifact_id="artifact-one",
        displayed_sha256="a" * 64,
        dimensions={"width": 512, "height": 512},
        item_id=None,
        version_id=None,
        shot_id=None,
        render_job_id=None,
        source_frame=None,
        media_artifact_id=None,
    )
    workflow = dict(operation_id="operation-one", job_id="job-one", version=version)
    samples = dict(
        validate_authored_document=dict(**wire, document_kind="sequence_map", path="map.json"),
        prepare_image_generation=prep,
        prepare_audio_generation=dict(**prep, duration_seconds=2),
        prepare_video_generation=prep,
        checkpoint_sources=dict(
            **operation, paths=["scene.blend"], message="Save", expected_head=None
        ),
        publish_outputs=dict(
            **wire,
            publication_id="publication-one",
            items=[
                dict(
                    **version,
                    kind="scene",
                    name="Scene",
                    expected_current_version_id=None,
                    source_commit="a" * 40,
                    artifact_ids=[],
                    preview_requests=[],
                    checkpoint_operation_id="operation-one",
                )
            ],
        ),
        request_preview=preview,
        set_preview_order=dict(**operation, version=version, job_ids=["job-one"]),
        register_artifact=dict(
            **operation,
            artifact_id="artifact-one",
            staging_path="output.png",
            kind="image",
            purpose="concept",
            sha256="a" * 64,
            media_type="image/png",
        ),
        submit_generation=dict(
            **wire,
            job_id="job-one",
            generation_task_id="task-one",
            target_id="target-one",
            provider_id="provider-one",
            capability="image",
            workflow_artifact_id="workflow-one",
            prompt_artifact_id="prompt-one",
            input_artifact_ids=[],
            seed=None,
            model_ids=["model-one"],
            settings={},
        ),
        request_vlm_review=dict(
            **wire,
            review_id="review-one",
            targets=[target],
            criteria="Check",
            telemetry_artifact_ids=[],
        ),
        escalate_vlm_review=dict(**operation, review_id="review-one", question="Is this correct?"),
        admit_job=dict(
            **job,
            kind="unity",
            target=dict(target_id="target-one", version=version, shot_id=None),
            input_digest="a" * 64,
            input_artifact_ids=[],
            execution_profile_id="profile-one",
        ),
        retry_job=dict(**job, expected_revision=1),
        update_job_state=dict(
            **job,
            attempt_id="attempt-one",
            expected_revision=1,
            state="running",
            availability="pending",
        ),
        get_job=dict(job_id="job-one", wait_seconds=30),
        get_vlm_review=dict(review_id="review-one"),
        get_artifact=dict(artifact_id="artifact-one", include_image=True),
        get_version=dict(version_id="version-one"),
        list_job_frames=dict(job_id="job-one", limit=100),
        search_web=dict(query="public query", limit=1),
        get_project_capabilities=dict(project_id="project-one"),
        list_items=dict(query="scene", cursor=None),
        assemble_sequence=workflow,
        run_unity_workflow=dict(
            **workflow,
            execution_profile_id="profile-one",
            project="unity/project",
            spawn=[0, 0, 0],
            camera="perspective",
            pipeline="urp",
            action="import",
        ),
        get_unity_result=dict(job_id="job-one"),
    )
    definitions = tools.definitions()
    assert samples.keys() == {tool.name for tool in definitions}
    for tool in definitions:
        name = tool.name
        if name in TOOLS:
            raw = SCHEMA["$defs"][TOOLS[name][2]]
        elif name in WORKFLOW_TOOLS:
            raw = _parameter_schema(name)
        else:
            raw = _read_schema(name)
        baseline = Draft202012Validator({**raw, "$defs": SCHEMA["$defs"]})
        projected = Draft202012Validator(tool.parameters)
        sample = samples[name]
        assert baseline.is_valid(sample) and projected.is_valid(sample), name
        assert not baseline.is_valid({**sample, "unexpected_key": True}), name
        assert not projected.is_valid({**sample, "unexpected_key": True}), name
        for key in sample:
            malformed = {**sample, key: {"__malformed": True}}
            assert baseline.is_valid(malformed) == projected.is_valid(malformed), (name, key)


def test_review_origin_guidance_does_not_echo_rejected_value(tools):
    text, result = failure(
        tools,
        "request_vlm_review",
        dict(
            schema_version="1.0",
            review_id="review-one",
            project_id="project-one",
            criteria="Check composition",
            telemetry_artifact_ids=[],
            targets=[
                dict(
                    origin="preview",
                    artifact_id="artifact-one",
                    displayed_sha256="a" * 64,
                    dimensions={"width": 512, "height": 512},
                    item_id=None,
                    version_id=None,
                    shot_id=None,
                    render_job_id=None,
                    source_frame=None,
                    media_artifact_id=None,
                )
            ],
        ),
    )
    assert_issue(
        result,
        "enum",
        "targets[0].origin",
        'Invalid value. Allowed values: "concept", "blender_render", "proxy_capture", "reference", "sequence_render"',
    )
    assert "preview" not in text


@pytest.fixture
def preview():
    return dict(
        schema_version="1.0",
        operation_id="operation-one",
        project_id="project-one",
        version={"item_id": "item-one", "version_id": "version-one"},
        spec=dict(
            intent_id="intent-one",
            shot_id=None,
            purpose="keyframe",
            source_closure_digest="a" * 64,
            scene_name="Scene",
            camera=None,
            frames={"start": 1, "end": 1},
            fps={"denominator": 1, "numerator": 24},
            dimensions={"width": 512, "height": 512},
            engine="CYCLES",
            device="CPU",
            samples=16,
            colour_management=dict(view_transform="Standard", look="None", exposure=0, gamma=1),
            renderer_revision="proscenium-blender-1",
            review_output=dict(
                bit_depth=8, encoding="view_transformed_srgb", media_type="image/png"
            ),
        ),
    )


def test_preview_guidance_names_nested_object_keys(tools, preview):
    preview["spec"].update(fps=24, review_output="png")
    text, result = failure(tools, "request_preview", preview)
    assert_issue(result, "type", "spec.fps", "Expected object with keys: denominator, numerator")
    assert_issue(
        result,
        "type",
        "spec.review_output",
        "Expected object with keys: bit_depth, encoding, media_type",
    )
    assert "24" not in text and "png" not in text


def test_preview_guidance_follows_union_refs(tools, preview):
    preview["spec"]["camera"] = "Cam_Front"
    text, result = failure(tools, "request_preview", preview)
    assert_issue(
        result,
        "anyOf",
        "spec.camera",
        "Expected one of: object with keys: aspect, camera_to_world, far_m, kind, near_m, "
        "orthographic_height_m, projection, shift, source_camera_id, space, vertical_fov_degrees | null",
    )
    assert "Cam_Front" not in text


def test_preview_guidance_keeps_exclusive_limits(tools, preview):
    preview["spec"]["colour_management"]["gamma"] = 0
    preview["spec"]["camera"] = dict(
        space="blender_world_m",
        kind="perspective",
        camera_to_world=[1] * 16,
        projection=[1] * 16,
        aspect=1,
        near_m=1,
        far_m=100,
        source_camera_id=None,
        vertical_fov_degrees=180,
    )
    _, result = failure(tools, "request_preview", preview)
    assert_issue(result, "exclusiveMinimum", "spec.colour_management.gamma", "Must be > 0")
    # The camera is under anyOf, so its branch error is reported as a union.
    assert safe_schema_guidance("exclusiveMaximum", "Must be < 180")
    error = DomainError(
        "invalid_request",
        issues=[
            dict(
                phase="schema",
                path="spec.camera.vertical_fov_degrees",
                code="exclusiveMaximum",
                message="Must be < 180",
            )
        ],
    )
    result = json.loads(tool_failure(error, "request_preview").text_result_for_llm)
    assert_issue(result, "exclusiveMaximum", "spec.camera.vertical_fov_degrees", "Must be < 180")


def test_generation_guidance_returns_only_static_pattern(tools):
    text, result = failure(
        tools,
        "submit_generation",
        dict(
            schema_version="1.0",
            job_id="job-one",
            project_id="project-one",
            generation_task_id="task-one",
            target_id="asset:bad",
            provider_id="provider-one",
            capability="image",
            workflow_artifact_id="workflow-one",
            prompt_artifact_id="prompt-one",
            input_artifact_ids=[],
            seed=None,
            model_ids=["model-one"],
            settings={},
        ),
    )
    assert_issue(
        result, "pattern", "target_id", "Must match pattern ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    )
    assert "asset:bad" not in text


def test_authored_document_guidance_names_required_union_branches(tools):
    _, result = failure(
        tools,
        "validate_authored_document",
        dict(
            schema_version="1.0",
            project_id="project-one",
            document_kind="sequence_map",
        ),
    )
    assert_issue(
        result, "oneOf", "$", "Expected one of: object requiring: path | object requiring: document"
    )


def test_unity_workflow_guidance_returns_enum_length_and_allowed_keys(tools):
    text, result = failure(
        tools,
        "run_unity_workflow",
        dict(
            operation_id="operation-one",
            job_id="job-one",
            execution_profile_id="profile-one",
            project="unity/project",
            camera="perspective",
            action="import",
            pipeline="URP",
            spawn=[0, 0],
            rejected_key="rejected_value",
        ),
    )
    assert_issue(result, "enum", "pipeline", 'Invalid value. Allowed values: "builtin", "urp"')
    assert_issue(result, "required", "version", "Missing required field version")
    assert_issue(result, "minItems", "spawn", "Must contain at least 3 item(s)")
    assert_issue(
        result,
        "additionalProperties",
        "$",
        "Unexpected property. Allowed keys: action, camera, execution_profile_id, job_id, operation_id, pipeline, project, scene_path, spawn, version",
    )
    assert all(value not in text for value in ("URP", "rejected_key", "rejected_value"))


@pytest.mark.parametrize(
    "name,args,path",
    [
        ("assemble_sequence", {"operation_id": "operation-one", "job_id": "job-one"}, "version"),
        ("get_unity_result", {}, "job_id"),
        ("get_version", {}, "version_id"),
        ("search_web", {}, "query"),
    ],
)
def test_workflow_and_read_required_guidance(tools, name, args, path):
    _, result = failure(tools, name, args)
    assert result["error"] == "invalid_request"
    assert_issue(result, "required", path, f"Missing required field {path}")


@pytest.mark.parametrize(
    "name,args,code,path,message",
    [
        (
            "get_job",
            {"job_id": "job-one", "wait_seconds": 99},
            "maximum",
            "wait_seconds",
            "Must be <= 30",
        ),
        (
            "get_vlm_review",
            {"review_id": "review-one", "wait_seconds": -1},
            "minimum",
            "wait_seconds",
            "Must be >= 0",
        ),
        ("list_items", {"limit": 500}, "maximum", "limit", "Must be <= 100"),
        (
            "list_job_frames",
            {"job_id": "job-one", "limit": 500},
            "maximum",
            "limit",
            "Must be <= 100",
        ),
        ("search_web", {"query": "public query", "limit": 0}, "minimum", "limit", "Must be >= 1"),
        (
            "get_artifact",
            {"artifact_id": "asset-one", "include_image": "true"},
            "type",
            "include_image",
            "Expected boolean",
        ),
        (
            "get_project_capabilities",
            {"private_field": 1},
            "additionalProperties",
            "$",
            "Unexpected property. Allowed keys: project_id",
        ),
        (
            "list_items",
            {"private_field": 1},
            "additionalProperties",
            "$",
            "Unexpected property. Allowed keys: cursor, limit, query",
        ),
    ],
)
def test_read_tool_guidance_reaches_tool_failure(tools, name, args, code, path, message):
    text, result = failure(tools, name, args)
    assert result["error"] == "invalid_request"
    assert_issue(result, code, path, message)
    assert "private_field" not in text


@pytest.mark.parametrize(
    "name,args", [("list_items", {}), ("list_job_frames", {"job_id": "job-one"})]
)
def test_non_string_cursor_remains_invalid_cursor(tools, name, args):
    args = {**args, "cursor": 1}
    with pytest.raises(DomainError) as error:
        read_arguments(name, args)
    assert error.value.code == "invalid_cursor"
    _, result = failure(tools, name, args)
    assert result["error"] == "invalid_cursor"
    assert_issue(result, "type", "cursor", "Expected string or null")


def test_search_still_cleans_queries(tools):
    assert read_arguments("search_web", {"query": "  public query  "}) == {
        "query": "public query",
        "limit": 8,
    }
    _, result = failure(tools, "search_web", {"query": "  "})
    message = "Web search query must be a non-empty string up to 512 characters"
    assert result["message"] == message
    assert_issue(result, "format", "query", message)


@pytest.mark.parametrize(
    "value", [2**54, float("nan"), float("inf")], ids=["large-int", "nan", "inf"]
)
def test_unity_canonicalisation_identifies_vector_member(tools, value):
    _, result = failure(
        tools,
        "run_unity_workflow",
        dict(
            operation_id="operation-one",
            job_id="job-one",
            execution_profile_id="profile-one",
            version={"item_id": "item-one", "version_id": "version-one"},
            project="unity/project",
            spawn=[value, 0, 0],
            camera="perspective",
            pipeline="urp",
            action="import",
        ),
    )
    assert result["error"] == "invalid_request"
    assert_issue(
        result, "type", "spawn[0]", "Number must be a finite JSON-safe value within IEEE-754 bounds"
    )


def test_checkpoint_dependent_required_round_trip(tools):
    _, result = failure(
        tools,
        "checkpoint_sources",
        dict(
            schema_version="1.0",
            operation_id="operation-one",
            project_id="project-one",
            paths=["scene.blend"],
            message="Save",
            expected_head=None,
            exclude_paths=["old.blend"],
        ),
    )
    assert_issue(
        result,
        "dependentRequired",
        "exclude_paths",
        "Field exclude_paths requires base_version, entry_path",
    )


def test_projected_const_enum_guidance_round_trip(preview):
    preview["schema_version"] = "rejected-version"
    schema = tool_schema("PreviewRequest")
    issues = schema_issues_for_schema(schema, preview)
    message = 'Invalid value. Allowed values: "1.0"'
    assert safe_schema_guidance("enum", message)
    text = tool_failure(
        DomainError("invalid_request", issues=issues), "request_preview"
    ).text_result_for_llm
    assert_issue(json.loads(text), "enum", "schema_version", message)
    assert "rejected-version" not in text


@pytest.mark.parametrize("map_keyword", ["patternProperties", "additionalProperties"])
def test_dynamic_map_key_is_redacted_at_its_schema_location(map_keyword):
    # `project_id` is a real property elsewhere and even a property in a
    # different allOf branch at this location, but not this validation descent.
    child = {"type": "object", "properties": {"fps": {"type": "integer"}}}
    mapping = {"^.*$": child} if map_keyword == "patternProperties" else child
    root = {
        "properties": {
            "slots": {
                "allOf": [
                    {map_keyword: mapping},
                    {"properties": {"project_id": {}}},
                ]
            }
        }
    }
    issues = schema_issues_for_schema(root, {"slots": {"project_id": {"fps": "bad"}}})
    assert issues == [
        dict(phase="schema", code="type", path="slots.field.fps", message="Expected integer")
    ]


@pytest.mark.parametrize("name", ["validate_authored_document", "publish_outputs"])
def test_authored_document_path_family_is_available_to_tool_failure(name):
    issues = schema_issues("AuthoredShotPlan", {"shots": [{"shot_id": 1}]})
    text = tool_failure(DomainError("invalid_request", issues=issues), name).text_result_for_llm
    assert_issue(json.loads(text), "type", "shots[0].shot_id", "Expected string")


@pytest.fixture
def sequence_without_clip():
    return dict(
        schema_version="1.0",
        sequence_version_id="sequence-one",
        project_id="project-one",
        fps={"numerator": 24, "denominator": 1},
        frame_count=1,
        silent=False,
        preserve_clip_audio=True,
        entries=[
            dict(
                entry_id="entry-one",
                shot_id=None,
                version_id="version-one",
                job_id="job-one",
                source_frames={"start": 1, "end": 1},
                sequence_frames={"start": 1, "end": 1},
                transform="identity",
                mapping_artifact_id=None,
            )
        ],
    )


def test_sequence_clip_audio_contains_issue_round_trip(tools, tmp_path, sequence_without_clip):
    tools.services.registry = SimpleNamespace(get=lambda _project_id: {"path": tmp_path})
    invocation = ToolInvocation(
        session_id="session-one",
        tool_call_id="call-one",
        tool_name="validate_authored_document",
        arguments=dict(
            schema_version="1.0",
            project_id="project-one",
            document_kind="sequence_map",
            document=sequence_without_clip,
        ),
    )
    response = asyncio.run(tools.invoke("validate_authored_document", invocation))
    authored_result = json.loads(response.text_result_for_llm)
    assert authored_result["schema_valid"] is False
    issue = dict(
        phase="schema",
        code="contains",
        path="entries",
        message="Preserving clip audio requires at least one clip entry",
    )
    assert authored_result["issues"] == [issue]

    error = DomainError(
        "invalid_request", issues=schema_issues("SequenceMap", sequence_without_clip)
    )
    response = tool_failure(error, "publish_outputs")
    assert json.loads(response.text_result_for_llm)["details"]["issues"] == [issue]


def test_generic_contains_guidance_uses_installed_item_shape(sequence_without_clip):
    branch = SCHEMA["$defs"]["SequenceMap"]["oneOf"][2]
    [issue] = schema_issues_for_schema(branch, sequence_without_clip)
    keys = sorted(SCHEMA["$defs"]["SequenceClipEntry"]["properties"])
    message = "Must contain at least one item matching: Expected object with keys: " + ", ".join(
        keys
    )
    assert issue == dict(phase="schema", code="contains", path="entries", message=message)
    assert safe_schema_guidance("contains", message)
    assert not safe_schema_guidance("contains", "Field failed contains validation")


def test_model_error_strips_fabricated_guidance_and_unknown_path_segments():
    unsafe = [
        ("enum", 'Invalid value. Allowed values: "rejected_value"'),
        ("additionalProperties", "Unexpected property. Allowed keys: private_field"),
        ("required", "Missing required field private_field"),
        ("rejected_value", "Field failed rejected_value validation"),
        ("maximum", "Field failed maximum validation"),
    ]
    issues = [
        dict(phase="schema", path="$", code=code, message=message) for code, message in unsafe
    ]
    for code, message in unsafe:
        assert not safe_schema_guidance(code, message)
    legitimate = dict(phase="schema", path="limit", code="maximum", message="Must be <= 100")
    issues += [
        {**legitimate, "path": "private_field"},
        {**legitimate, "path": "execution_profile_id"},
        {**legitimate, "path": "query.limit"},
        {**legitimate, "path": "limit[0]"},
        legitimate,
    ]
    text = tool_failure(
        DomainError("invalid_request", issues=issues), "list_items"
    ).text_result_for_llm
    assert json.loads(text)["details"]["issues"] == [legitimate]
    assert "rejected_value" not in text and "private_field" not in text
