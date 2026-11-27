"""Model guidance is opt-in, static host text. All runtime regressions are offline."""

import ast
import json
from collections import Counter
from dataclasses import replace
from importlib.util import resolve_name
from types import SimpleNamespace

import httpx
import pytest
from proscenium.api.errors import DomainError
from proscenium.contracts import canonical
from proscenium.copilot.tools import ERROR_GUIDANCE, tool_failure
from proscenium.guidance import LEGACY_SAFE_REASONS, REVIEWED_SAFE_REASONS
from proscenium.jobs.resources import Resources
from proscenium.providers import budget3d, hunyuan, klein
from proscenium.providers.plugins import Budget3DPlugin, ImagePlugin
from proscenium.reviews.store import ReviewStore
from test_provider_klein_and_pump import raster
from test_rc4_retry_and_context import publish
from test_worker_capabilities_and_publication import PROJECT, RECIPE, ROOT, TASK, invoke
from test_worker_capabilities_and_publication import offline as offline  # noqa: F401
from test_worker_capabilities_and_publication import runtime as runtime  # noqa: F401

BACKEND = ROOT / "apps/backend/src"
TOOL_PACKAGES = {"providers", "previews", "reviews", "catalogue", "sources", "workflows", "jobs"}
# Worker wiring, journal internals and recovery invariants are not request guidance.
INTERNAL_ONLY_MESSAGES = {
    ("providers.minimax_h3", "Invalid video verifier memory ceiling"),
    ("providers.service", "Review outbox payload does not match attempt"),
    ("providers.service", "Artifact registrar is not bound"),
    ("providers.service", "Missing frozen generation plugin"),
    ("previews.runner", "Sealed assembly delivery or manifest is missing"),
    ("previews.runner", "Sealed assembly manifest is malformed"),
    ("previews.runner", "Invalid supervisor terminal journal"),
    ("previews.runner", "Invalid preview journal"),
}


def literal_strings(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        yes, no = literal_strings(node.body), literal_strings(node.orelse)
        if yes and no:
            return yes | no
    return set()


class Module:
    def __init__(self, path):
        self.path = path
        parts = path.relative_to(BACKEND).with_suffix("").parts
        self.name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        package = self.name if parts[-1] == "__init__" else self.name.rpartition(".")[0]
        self.tree = ast.parse(path.read_bytes(), filename=str(path))
        self.constants = {}
        self.imports = {}
        self.error_names = {"DomainError"}
        bindings = Counter()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bindings[node.id] += 1
            elif isinstance(node, ast.arg):
                bindings[node.arg] += 1
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings[node.name] += 1
            elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
                bindings[node.name] += 1
            elif isinstance(node, ast.MatchMapping) and node.rest:
                bindings[node.rest] += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    bindings[bound] += 1
                    if isinstance(node, ast.ImportFrom) and alias.name == "DomainError":
                        self.error_names.add(bound)

        for node in self.tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and bindings[target.id] == 1:
                        self.constants[target.id] = literal_strings(node.value)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    if bindings[bound] != 1:
                        continue
                    imported = alias.name if alias.asname else alias.name.split(".")[0]
                    if isinstance(node, ast.ImportFrom):
                        base = resolve_name("." * node.level + (node.module or ""), package)
                        imported = f"{base}.{alias.name}"
                    self.imports[bound] = imported

    def constant_path(self, node):
        attrs = []
        while isinstance(node, ast.Attribute):
            attrs.insert(0, node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            if not attrs and node.id in self.constants:
                return self.name, node.id
            if node.id in self.imports:
                module, _, constant = ".".join([self.imports[node.id], *attrs]).rpartition(".")
                return module, constant
        return None

    def messages(self, node, modules):
        # A local variable, re-bound module constant or function call cannot opt in.
        if isinstance(node, ast.Constant):
            return literal_strings(node)
        reference = self.constant_path(node)
        if reference and reference[0] in modules:
            module, constant = reference
            return modules[module].constants.get(constant, set())
        return set()

    def error_calls(self):
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            keywords = {kw.arg: kw.value for kw in node.keywords}
            if not (
                (isinstance(func, ast.Name) and func.id in self.error_names)
                or (isinstance(func, ast.Attribute) and func.attr == "DomainError")
                or "safe_for_model" in keywords
            ):
                continue
            message = keywords.get("message", node.args[2] if len(node.args) > 2 else None)
            yield node, message, keywords

    def attribute_mutations(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
                yield node, node.attr
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"setattr", "delattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                yield node, node.args[1].value


@pytest.fixture(scope="module")
def modules():
    parsed = [Module(path) for path in sorted((BACKEND / "proscenium").rglob("*.py"))]
    return {module.name: module for module in parsed}


def test_model_guidance_is_statically_authored(modules):
    violations = []
    opted_in, static_messages = set(), set()
    for module in modules.values():
        for call, message, keywords in module.error_calls():
            values = module.messages(message, modules)
            static_messages.update(values)
            where = f"{module.path.relative_to(ROOT)}:{call.lineno}"
            flag = keywords.get("safe_for_model")
            if None in keywords or any(isinstance(arg, ast.Starred) for arg in call.args):
                violations.append(f"{where}: use explicit DomainError arguments")
            if flag is not None and not (
                isinstance(flag, ast.Constant) and type(flag.value) is bool
            ):
                violations.append(f"{where}: safe_for_model must be a literal bool")
            if isinstance(flag, ast.Constant) and flag.value is True:
                if not values or "" in values:
                    violations.append(f"{where}: model guidance must be a non-empty static string")
                elif not values <= REVIEWED_SAFE_REASONS:
                    violations.append(
                        f"{where}: unreviewed model guidance: {values - REVIEWED_SAFE_REASONS}"
                    )
                opted_in.update(values)
    assert not violations, "\n".join(violations)
    assert LEGACY_SAFE_REASONS <= static_messages - opted_in
    assert opted_in == REVIEWED_SAFE_REASONS - LEGACY_SAFE_REASONS


def test_reviewed_catalogue_contains_only_literals(modules):
    definitions = {
        node.target.id: node.value
        for node in modules["proscenium.guidance"].tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for name in ("LEGACY_SAFE_REASONS", "_OPTED_IN_SAFE_REASONS"):
        call = definitions[name]
        assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        assert call.func.id == "frozenset" and len(call.args) == 1 and not call.keywords
        values = call.args[0]
        assert isinstance(values, ast.Set)
        assert all(
            isinstance(value, ast.Constant) and type(value.value) is str for value in values.elts
        )
    assert (
        ast.unparse(definitions["REVIEWED_SAFE_REASONS"])
        == "LEGACY_SAFE_REASONS | _OPTED_IN_SAFE_REASONS"
    )
    assert type(REVIEWED_SAFE_REASONS) is frozenset


def test_error_fields_and_reviewed_constant_attributes_are_not_reassigned(modules):
    protected = {
        "message",
        "safe_for_model",
        "REVIEWED_SAFE_REASONS",
        "LEGACY_SAFE_REASONS",
        "_OPTED_IN_SAFE_REASONS",
    }
    for module in modules.values():
        for _, message, keywords in module.error_calls():
            flag = keywords.get("safe_for_model")
            if isinstance(flag, ast.Constant) and flag.value is True:
                reference = module.constant_path(message)
                if reference:
                    protected.add(reference[1])

    errors = modules["proscenium.api.errors"]
    domain_error = next(
        node
        for node in errors.tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DomainError"
    )
    constructor = next(
        node
        for node in domain_error.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    initialisers = {
        target
        for node in constructor.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
        and target.attr in {"message", "safe_for_model"}
    }
    violations = [
        f"{module.path.relative_to(ROOT)}:{node.lineno}: protected attribute .{name} was changed"
        for module in modules.values()
        for node, name in module.attribute_mutations()
        # Reserve these attribute names across the backend so a receiver alias cannot hide a write.
        if name in protected and node not in initialisers
    ]
    assert not violations, "\n".join(violations)


def test_tool_package_static_messages_are_classified(modules):
    unclassified = []
    internal = set()
    for module in modules.values():
        package = module.name.split(".")[1:2]
        if not package or package[0] not in TOOL_PACKAGES:
            continue
        for call, message, keywords in module.error_calls():
            flag = keywords.get("safe_for_model")
            opted_in = isinstance(flag, ast.Constant) and flag.value is True
            for value in module.messages(message, modules) | literal_strings(message):
                entry = (module.name.removeprefix("proscenium."), value)
                if entry in INTERNAL_ONLY_MESSAGES:
                    assert not opted_in and value not in REVIEWED_SAFE_REASONS, entry
                    internal.add(entry)
                elif not opted_in and value not in REVIEWED_SAFE_REASONS:
                    unclassified.append(f"{module.path.relative_to(ROOT)}:{call.lineno}: {value!r}")
    assert not unclassified, "\n".join(unclassified)
    assert internal == INTERNAL_ONLY_MESSAGES, "Remove stale internal-only exceptions"


def test_private_error_message_stays_out_of_tool_result_and_flag_stays_off_wire():
    error = DomainError(
        "invalid_request", message="leaked /home/secret/token", safe_for_model=False
    )
    result = tool_failure(error, "submit_generation")
    assert result.result_type == "failure"
    assert json.loads(result.text_result_for_llm) == {
        "error": "invalid_request",
        "message": ERROR_GUIDANCE["invalid_request"],
        "details": {},
    }
    safe = DomainError("invalid_request", message="Use a published version", safe_for_model=True)
    ordinary = DomainError("invalid_request", message="Use a published version")
    assert safe.envelope() == ordinary.envelope()
    assert not DomainError("invalid_request", safe_for_model=True).safe_for_model


@pytest.mark.parametrize(
    ("tool", "code"),
    [
        ("prepare_image_generation", "scope_denied"),
        ("prepare_audio_generation", "invalid_request"),
        ("validate_authored_document", "invalid_request"),
    ],
)
def test_explicit_guidance_is_not_overridden(tool, code):
    error = DomainError(
        code, message="Omit target_slot instead of passing null", safe_for_model=True
    )
    assert json.loads(tool_failure(error, tool).text_result_for_llm)["message"] == error.message


def assert_private_reason_stripped(error):
    result = tool_failure(error, "submit_generation")
    assert json.loads(result.text_result_for_llm)["message"] == ERROR_GUIDANCE["invalid_request"]


def test_mutating_an_opted_in_message_does_not_make_it_reviewed():
    error = DomainError("invalid_request", message=budget3d.INVALID_GRAPH, safe_for_model=True)
    error.message = "leaked /etc/secret"
    assert_private_reason_stripped(error)


def test_mutating_the_flag_does_not_make_a_message_reviewed():
    error = DomainError("invalid_request", message="leaked secret")
    error.safe_for_model = True
    assert_private_reason_stripped(error)


def test_mutating_an_imported_constant_does_not_change_the_reviewed_catalogue(monkeypatch):
    monkeypatch.setattr(budget3d, "INVALID_GRAPH", "leaked secret")
    with pytest.raises(DomainError) as error:
        Budget3DPlugin().bind_graph({}, "Lantern", "42", {"ref": "ref.png"}, budget3d.models())
    assert error.value.safe_for_model and error.value.message == "leaked secret"
    assert_private_reason_stripped(error.value)


def test_assembly_guidance_does_not_parse_mutated_error_text():
    error = DomainError("invalid_request", 422)
    error.message = "Unsupported assembly execution_profile_id; omit it or use 'secret-token'"
    result = tool_failure(error, "assemble_sequence")
    assert json.loads(result.text_result_for_llm)["message"] == ERROR_GUIDANCE["invalid_request"]


def register(runtime, identity, data, kind, purpose, media_type, **kwargs):
    artifacts = runtime.services.extension("artifacts")
    artifact = artifacts.promote(
        PROJECT, identity, data, kind=kind, purpose=purpose, media_type=media_type, **kwargs
    )
    with runtime.store.transaction() as tx:
        artifacts.put(tx, artifact)
    return artifact


def submission(runtime, graph, **overrides):
    register(runtime, "workflow", canonical(graph), "document", "metadata", "application/json")
    register(runtime, "prompt", b"Draw a lantern", "document", "metadata", "text/plain")
    return {
        **dict(
            schema_version="1.0",
            job_id="job-guide",
            project_id=PROJECT,
            generation_task_id=TASK,
            target_id=TASK,
            provider_id="comfy",
            capability="image",
            workflow_artifact_id="workflow",
            prompt_artifact_id="prompt",
            input_artifact_ids=[],
            seed="42",
            model_ids=[RECIPE[k] for k in ("unet", "clip", "vae")],
            settings={},
        ),
        **overrides,
    }


def assert_failure(runtime, name, request, code, message):
    outcome, result_type = invoke(runtime, name, request)
    assert result_type == "failure", outcome
    assert (outcome["error"], outcome["message"]) == (code, message)
    assert not runtime.calls


@pytest.mark.parametrize(
    ("overrides", "code", "message"),
    [
        (
            {"settings": {"budget": 1}},
            "invalid_request",
            "settings accepts only target_slot; pass {} when no target_slot is needed",
        ),
        (
            {"settings": {"target_slot": None}},
            "invalid_request",
            "Omit target_slot instead of passing null",
        ),
        (
            {"target_id": "hero-ryzen-ai-max"},
            "scope_denied",
            "Unstructured target must name its task: set target_id to generation_task_id (or the active new_item intent_id) when no published item exists yet",
        ),
        (
            {"generation_task_id": "another-task"},
            "scope_denied",
            "generation_task_id does not match the active work",
        ),
        (
            {"seed": "9007199254740992"},
            "invalid_request",
            "An exact JSON-safe decimal seed is required",
        ),
    ],
)
def test_submit_generation_admission_guidance(runtime, overrides, code, message):
    assert_failure(
        runtime, "submit_generation", submission(runtime, {}, **overrides), code, message
    )


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("prefix", "Budget3D SaveGLB node 15 filename_prefix must be 'mesh/proscenium-budget3d'"),
        ("seed", "Budget3D seed must be an exact JSON-safe decimal integer"),
        ("null-seed", "Budget3D seed must be an exact JSON-safe decimal integer"),
        ("models", "One conditioning image and exact Budget3D models required"),
        ("unknown", "Budget3D workflow graph must match the pinned trusted template"),
    ],
)
def test_submit_budget3d_guidance(runtime, monkeypatch, fault, message):
    runtime.services.settings.services[0]["capabilities"].append("image_to_3d")
    register(runtime, "reference", raster("solid"), "image", "reference", "image/png")
    graph = budget3d.graph("reference")
    models, seed = budget3d.models(), "42"
    if fault == "prefix":
        graph["15"]["inputs"]["filename_prefix"] = "mesh/custom"
    elif fault == "seed":
        seed = "9007199254740992"
    elif fault == "null-seed":
        seed = None
    elif fault == "models":
        models = ["not-a-model"]
    elif fault == "unknown":

        def private_failure(*args, **kwargs):
            raise ValueError("leaked /home/secret/token")

        monkeypatch.setattr(budget3d, "bind", private_failure)
    request = submission(
        runtime,
        graph,
        capability="image_to_3d",
        input_artifact_ids=["reference"],
        model_ids=models,
        seed=seed,
    )
    assert_failure(runtime, "submit_generation", request, "invalid_request", message)


def test_submit_hunyuan_with_null_seed_reports_seed_guidance_without_provider_calls(runtime):
    comfy, vlm = runtime.services.settings.services
    runtime.services.settings = replace(
        runtime.services.settings,
        services=({**comfy, "provider_id": "local-comfy", "capabilities": ["image_to_3d"]}, vlm),
    )
    runtime.provider.options["mesh_profiles"] = {"local-comfy": "hunyuan3d-native-2.0-1"}
    register(runtime, "reference", raster("solid"), "image", "reference", "image/png")
    request = submission(
        runtime,
        hunyuan.graph("reference"),
        provider_id="local-comfy",
        capability="image_to_3d",
        input_artifact_ids=["reference"],
        model_ids=[hunyuan.FILENAME],
        seed=None,
    )
    assert_failure(
        runtime,
        "submit_generation",
        request,
        "invalid_request",
        "An exact JSON-safe decimal seed is required",
    )


@pytest.mark.parametrize(
    ("graph", "message"),
    [
        ([], "Workflow artefact must be a valid UTF-8 JSON object"),
        ({"invalid": None}, "Malformed API graph"),
    ],
)
def test_submit_generation_malformed_workflow_guidance(runtime, graph, message):
    assert_failure(
        runtime, "submit_generation", submission(runtime, graph), "invalid_request", message
    )


@pytest.mark.parametrize(
    ("fault", "code", "reason"),
    [
        ("input", "invalid_request", "Missing graph input"),
        ("models", "invalid_request", "Model manifest/output graph mismatch"),
        ("black", "corrupt_provider_output", "Provider returned a corrupted all-zero or NaN image"),
        ("private-domain", "invalid_request", None),
        ("unexpected", "provider_unavailable", None),
    ],
)
def test_pump_persists_only_reviewed_reasons_for_get_job(runtime, fault, code, reason):
    graph = klein.graph(**{k: RECIPE[k] for k in ("unet", "clip", "vae")}, width=512, height=512)
    overrides = {}
    if fault == "input":
        del graph["model"]["inputs"]["weight_dtype"]
    elif fault == "models":
        overrides["model_ids"] = ["not-the-loaded-model"]
    job, result_type = invoke(runtime, "submit_generation", submission(runtime, graph, **overrides))
    assert result_type == "success", job
    assert not runtime.calls
    runtime.services.resources = Resources(runtime.store)
    runtime.provider.registrar = lambda *_: pytest.fail("Failed jobs must not register artefacts")

    def transport(request):
        path = request.url.path
        runtime.calls.append(path)
        if path == "/object_info":
            if fault == "private-domain":
                raise DomainError("invalid_request", message="leaked /home/secret/token")
            if fault == "unexpected":
                raise RuntimeError("leaked /home/secret/token")
            return httpx.Response(200, json=runtime.catalog)
        assert fault == "black", f"Unexpected dispatch: {path}"
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"vram_free": 2**30}]})
        if path == "/prompt":
            assert request.method == "POST"
            return httpx.Response(200, json={"prompt_id": "prompt-one"})
        if path == "/history/prompt-one":
            return httpx.Response(
                200,
                json={
                    "prompt-one": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {"save": {"images": [{"filename": "generated.png"}]}},
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=raster("black"))
        pytest.fail(f"Unexpected provider call: {path}")

    runtime.provider.client.close()
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        runtime.provider.client = client
        (settled,) = runtime.provider.pump(PROJECT, job["job_id"])
    assert runtime.calls == (
        ["/object_info", "/queue", "/system_stats", "/prompt", "/history/prompt-one", "/view"]
        if fault == "black"
        else ["/object_info"]
    )
    observed, result_type = invoke(runtime, "get_job", {"job_id": job["job_id"]}, "get-job")
    assert result_type == "success", observed
    assert observed == settled
    assert (observed["state"], observed["error_code"], observed["error_reason"]) == (
        "failed",
        code,
        reason,
    )


def test_job_update_rejects_unreviewed_reasons_and_clears_previous_reason(runtime):
    graph = klein.graph(**{k: RECIPE[k] for k in ("unet", "clip", "vae")}, width=512, height=512)
    job, result_type = invoke(runtime, "submit_generation", submission(runtime, graph))
    assert result_type == "success", job
    updated = runtime.provider.update(
        job, "outcome_uncertain", error_code="provider_unavailable", error_reason="leaked secret"
    )
    assert updated["error_reason"] is None
    # Exercise the transaction-bound reducer as well as the ordinary update path.
    with runtime.store.transaction() as tx:
        updated = runtime.provider.update(
            updated,
            "outcome_uncertain",
            tx=tx,
            error_code="invalid_request",
            error_reason="Missing graph input",
        )
    assert updated["error_reason"] == "Missing graph input"
    recovered = runtime.provider.update(updated, "running", external_job_id="observed-prompt")
    assert recovered["error_code"] is None and "error_reason" not in recovered


def test_request_vlm_review_cross_version_guidance(runtime):
    runtime.services.settings = replace(
        runtime.services.settings,
        services=(
            runtime.services.settings.services[0],
            {
                **runtime.services.settings.services[1],
                "available": True,
                "model_id": "vision-fixture",
            },
        ),
    )
    runtime.services.extensions["reviews"] = ReviewStore(runtime.services)
    runtime.services.extensions["vlm"] = SimpleNamespace(request=runtime.provider.request_review)
    artefact = register(
        runtime,
        "another-version-image",
        raster("solid"),
        "image",
        "concept",
        "image/png",
        metadata={"source_version_id": "v1"},
    )
    first = {"item_id": "lantern", "version_id": "v1"}
    second = {**first, "version_id": "v2"}
    publish(runtime, first, "asset", artifact_ids=[artefact["artifact_id"]])
    publish(runtime, second, "asset")
    target = dict(
        **second,
        origin="concept",
        artifact_id=artefact["artifact_id"],
        displayed_sha256=artefact["sha256"],
        dimensions=artefact["dimensions"],
        shot_id=None,
        render_job_id=None,
        source_frame=None,
        media_artifact_id=None,
    )
    request = dict(
        schema_version="1.0",
        project_id=PROJECT,
        review_id="review-guide",
        targets=[target],
        criteria="Check form",
        telemetry_artifact_ids=[],
    )
    assert_failure(
        runtime,
        "request_vlm_review",
        request,
        "source_conflict",
        "Artifact belongs to a different version. Only concept and reference images published on the target version may be reused.",
    )


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("black", "Provider returned a corrupted all-zero or NaN image"),
        (
            "quadrant_wipeout",
            "Provider returned a corrupted image with two or more dead black quadrants",
        ),
        ("scanline", "Provider returned a corrupted scanline or noise image"),
        ("noise", "Provider returned a corrupted scanline or noise image"),
    ],
)
def test_image_corruption_guidance(kind, message):
    with pytest.raises(DomainError) as error:
        ImagePlugin().inspect_output(raster(kind), 16384)
    assert error.value.safe_for_model
    result = tool_failure(error.value, "submit_generation")
    assert result.result_type == "failure"
    assert json.loads(result.text_result_for_llm) == {
        "error": "corrupt_provider_output",
        "message": message,
        "details": {},
    }
