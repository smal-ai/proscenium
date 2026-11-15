"""Pure classification of saved modifier graphs. No bpy import or graph mutation.

Every node in an assigned geometry group is checked, including disconnected/muted
nodes. Unassigned node groups do not become modifier dependencies. This deliberately
conservative rule avoids trying to infer Blender's socket/zone execution semantics.
"""

SIMULATION_MODIFIERS = frozenset(
    {"CLOTH", "FLUID", "DYNAMIC_PAINT", "PARTICLE_SYSTEM", "SOFT_BODY"}
)
UNSUPPORTED_NODE_FAMILIES = (
    "GeometryNodeSimulation",
    "GeometryNodeBake",
    "GeometryNodeCache",
    "GeometryNodeImport",
)


def modifier_kind(modifier) -> str:
    """Classify native modifiers without confusing Geometry Nodes with physics."""
    if modifier.type == "NODES":
        return "geometry_nodes"
    if modifier.type in SIMULATION_MODIFIERS:
        return "unsupported_simulation_cache"
    return "ordinary"


def reachable_geometry_groups(modifier) -> list:
    """Validate the complete assigned/nested graph and return its distinct groups.

    Stateless native fields, primitives, transforms, joins, repeats and explicit
    Realize Instances are ordinary evaluation. Simulation/bake/cache/file-import
    node families need a separate immutable cache/input contract and remain refused.
    This is not a general claim about arbitrary add-on nodes or unresolved instances.
    """
    if modifier_kind(modifier) != "geometry_nodes":
        return []
    root = getattr(modifier, "node_group", None)
    if root is None:
        raise ValueError("unsupported_geometry_nodes: modifier has no saved node group")
    result, visited, active = [], set(), set()

    def visit(group):
        pointer = getattr(group, "as_pointer", None)
        key = pointer() if callable(pointer) else id(group)
        if key in active:
            raise ValueError("unsupported_geometry_nodes: recursive node-group dependency")
        if key in visited:
            return
        active.add(key)
        result.append(group)
        for node in group.nodes:
            identifier = getattr(node, "bl_idname", "")
            if identifier.startswith(UNSUPPORTED_NODE_FAMILIES):
                raise ValueError(
                    f"unsupported_geometry_nodes: {identifier} needs an immutable simulation/bake/cache/import contract"
                )
            if identifier == "NodeUndefined":
                raise ValueError("unsupported_geometry_nodes: undefined saved node type")
            child = getattr(node, "node_tree", None)
            if child is not None:
                visit(child)
            elif getattr(node, "type", "") == "GROUP":
                raise ValueError("unsupported_geometry_nodes: nested group has no saved node tree")
        active.remove(key)
        visited.add(key)

    visit(root)
    return result
