"""Retry-safe machine/project projection of W01's durable native association.

No native calls and no invented session identities. Machine CAS + project operation
form a recoverable saga because the stores deliberately have separate transactions.
"""

from typing import TYPE_CHECKING, Any

from proscenium.api.errors import DomainError

if TYPE_CHECKING:
    from proscenium.services import Services


def reconcile(services: "Services", project_id: str) -> None:
    with services.activation_lock:
        if project_id in services.projection_running:
            return
        services.projection_running.add(project_id)
        try:
            _reconcile(services, project_id)
        finally:
            services.projection_running.remove(project_id)


def _apply(services: "Services", pending: dict[str, Any]) -> None:
    project_id, record = pending["record"]["project_id"], pending["record"]
    store = services.registry.project_store(project_id, active=False)
    with store.transaction() as tx:

        def effect() -> dict[str, Any]:
            control = tx.get("copilot.control", "gate", required=False)
            # Only carry forward a previously matching activation fence. A genuinely
            # stale activation is never repaired without W01's explicit Resume.
            if control and control.get("activation_revision") == pending["previous_revision"]:
                tx.put(
                    "copilot.control",
                    "gate",
                    {**control, "activation_revision": record["state_revision"]},
                    expected_revision=tx.revision("copilot.control", "gate"),
                )
            tx.emit("project.state_changed", record, actor={"kind": "system", "id": "project-host"})
            return {"emitted": True}

        tx.operation("host.project_projection", pending["identity"], record, effect)


def _reconcile(services: "Services", project_id: str) -> None:
    registry = services.registry
    with registry.store.transaction(write=False) as machine:
        pending = machine.get("host.project_projection", project_id, required=False)
    if pending and not pending["applied"]:
        _apply(services, pending)
        with registry.store.transaction() as machine:
            machine.put(
                "host.project_projection",
                project_id,
                {**pending, "applied": True},
                expected_revision=machine.revision("host.project_projection", project_id),
            )

    record = registry.get(project_id)
    with registry.project_store(project_id, active=False).transaction(write=False) as tx:
        association = tx.get("copilot.session", "native", required=False)
        control = tx.get("copilot.control", "gate", required=False) or {}
    session_id = association["session_id"] if association else record["native_session_id"]
    if record["native_session_id"] and session_id != record["native_session_id"]:
        services.gate.close(project_id)
        raise DomainError("source_conflict", 409, "Native session identity changed")
    state = record["state"]
    if registry.active_id() == project_id:
        old_epoch = bool(
            association and association["owner_epoch"] != services.ownership.epoch
        ) or bool(control.get("epoch") and control["epoch"] != services.ownership.epoch)
        if control.get("fenced") or old_epoch:
            state = "awaiting_resume"
        elif (association and not old_epoch) or control.get(
            "reconciled_epoch"
        ) == services.ownership.epoch:
            state = "active"
    else:
        state = "inactive"
    if record["native_session_id"] == session_id and record["state"] == state:
        return
    updated = {
        **record,
        "state": state,
        "native_session_id": session_id,
        "state_revision": record["state_revision"] + 1,
    }
    pending = {
        "record": updated,
        "previous_revision": record["state_revision"],
        "identity": f"{project_id}:{updated['state_revision']}",
        "applied": False,
    }
    with registry.store.transaction() as machine:
        machine.put(
            "projects",
            project_id,
            updated,
            expected_revision=record["state_revision"],
            schema="ProjectRecord",
        )
        previous = machine.get("host.project_projection", project_id, required=False)
        machine.put(
            "host.project_projection",
            project_id,
            pending,
            expected_revision=machine.revision("host.project_projection", project_id)
            if previous
            else None,
        )
    _apply(services, pending)
    with registry.store.transaction() as machine:
        machine.put(
            "host.project_projection",
            project_id,
            {**pending, "applied": True},
            expected_revision=machine.revision("host.project_projection", project_id),
        )
