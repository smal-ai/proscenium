"""Pure graph classification. These doubles are not native evaluation evidence."""

from types import SimpleNamespace as NS

import pytest
from proscenium.previews.geometry_nodes import modifier_kind, reachable_geometry_groups


def node(identifier, child=None):
    return NS(bl_idname=identifier, type="GROUP" if child is not None else "OTHER", node_tree=child)


def group(name, *nodes):
    return NS(name=name, nodes=list(nodes))


def modifier(tree):
    return NS(type="NODES", node_group=tree)


def test_nodes_modifier_is_not_classified_as_simulation():
    assert modifier_kind(modifier(group("cube", node("GeometryNodeMeshCube")))) == "geometry_nodes"
    assert modifier_kind(NS(type="CLOTH")) == "unsupported_simulation_cache"
    assert modifier_kind(NS(type="SUBSURF")) == "ordinary"


def test_stateless_nested_transform_join_realize_preserves_graph():
    inner = group("saved parameters", node("GeometryNodeMeshCube"), node("GeometryNodeTransform"))
    outer = group(
        "authored generator",
        node("GeometryNodeGroup", inner),
        node("GeometryNodeGroup", inner),
        node("GeometryNodeJoinGeometry"),
        node("GeometryNodeRealizeInstances"),
    )
    before = [id(n) for n in outer.nodes]
    assert reachable_geometry_groups(modifier(outer)) == [outer, inner]
    assert before == [id(n) for n in outer.nodes]
    assert outer.nodes[0].node_tree is inner


@pytest.mark.parametrize(
    "identifier",
    [
        "GeometryNodeSimulationInput",
        "GeometryNodeSimulationOutput",
        "GeometryNodeBake",
        "GeometryNodeCache",
        "GeometryNodeImportOBJ",
        "GeometryNodeImportPLY",
        "GeometryNodeImportSTL",
        "GeometryNodeImportCSV",
        "GeometryNodeImportText",
    ],
)
def test_reachable_nested_unsupported_nodes_are_explicitly_rejected(identifier):
    leaf = group("nested unsupported", node(identifier))
    middle = group("middle", node("GeometryNodeGroup", leaf))
    outer = group("generator", node("GeometryNodeGroup", middle))
    with pytest.raises(ValueError, match=identifier):
        reachable_geometry_groups(modifier(outer))


def test_unassigned_group_does_not_make_another_modifier_stateful():
    unused = group("not assigned", node("GeometryNodeSimulationOutput"))
    used = group("assigned cube", node("GeometryNodeMeshCube"))
    assert unused not in reachable_geometry_groups(modifier(used))


def test_disconnected_and_muted_unsupported_node_is_still_refused():
    value = node("GeometryNodeBake")
    value.mute = True
    with pytest.raises(ValueError, match="GeometryNodeBake"):
        reachable_geometry_groups(
            modifier(group("conservative validation", node("GeometryNodeMeshCube"), value))
        )


def test_missing_saved_group_and_recursive_graph_are_not_silently_accepted():
    with pytest.raises(ValueError, match="no saved node group"):
        reachable_geometry_groups(modifier(None))
    recursive = group("invalid recursive")
    recursive.nodes.append(node("GeometryNodeGroup", recursive))
    with pytest.raises(ValueError, match="recursive"):
        reachable_geometry_groups(modifier(recursive))


def test_undefined_and_missing_nested_group_are_refused():
    with pytest.raises(ValueError, match="undefined"):
        reachable_geometry_groups(modifier(group("undefined", node("NodeUndefined"))))
    with pytest.raises(ValueError, match="nested group"):
        reachable_geometry_groups(
            modifier(
                group("missing", NS(bl_idname="GeometryNodeGroup", type="GROUP", node_tree=None))
            )
        )
