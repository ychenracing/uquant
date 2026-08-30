"""Private deterministic graph mechanics for the public reachability owner."""

from __future__ import annotations

from collections.abc import Sequence

StateKey = tuple[object, ...]


def strong_components(
    nodes: set[StateKey],
    edges: set[tuple[StateKey, StateKey]],
) -> tuple[tuple[StateKey, ...], ...]:
    """Return deterministic strongly connected components."""

    outgoing: dict[StateKey, set[StateKey]] = {node: set() for node in nodes}
    incoming: dict[StateKey, set[StateKey]] = {node: set() for node in nodes}
    for source, destination in edges:
        outgoing[source].add(destination)
        incoming[destination].add(source)
    finish = _finish_order(nodes=nodes, outgoing=outgoing)
    components: list[tuple[StateKey, ...]] = []
    assigned: set[StateKey] = set()
    for root in reversed(finish):
        if root in assigned:
            continue
        component: set[StateKey] = set()
        stack = [root]
        assigned.add(root)
        while stack:
            node = stack.pop()
            component.add(node)
            for parent in incoming[node]:
                if parent not in assigned:
                    assigned.add(parent)
                    stack.append(parent)
        components.append(tuple(sorted(component, key=repr)))
    return tuple(components)


def _finish_order(
    *,
    nodes: set[StateKey],
    outgoing: dict[StateKey, set[StateKey]],
) -> list[StateKey]:
    visited: set[StateKey] = set()
    finish: list[StateKey] = []
    for root in sorted(nodes, key=repr):
        if root in visited:
            continue
        stack: list[tuple[StateKey, bool]] = [(root, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                finish.append(node)
                continue
            if node in visited:
                continue
            visited.add(node)
            stack.append((node, True))
            stack.extend(
                (child, False)
                for child in sorted(outgoing[node], key=repr, reverse=True)
                if child not in visited
            )
    return finish


def maximum_consecutive_component_sessions(
    *,
    members: set[StateKey],
    nodes: Sequence[StateKey],
    sessions: Sequence[str],
    healthy: Sequence[bool],
) -> int:
    """Count unique consecutive healthy sessions without crossing a gap."""

    maximum = 0
    current = 0
    last_counted_session = ""
    for node, session, is_healthy in zip(nodes, sessions, healthy, strict=True):
        if node not in members or not is_healthy:
            current = 0
            last_counted_session = ""
            continue
        if session != last_counted_session:
            current += 1
            last_counted_session = session
            maximum = max(maximum, current)
    return maximum


__all__ = ()
