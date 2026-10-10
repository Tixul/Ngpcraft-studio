"""Conservative scene reachability for the compile plan, not runtime support.

Both conditional branches are retained. Resolved init/hook threads include
prefab overrides; custom scripts are visited per owning scene to retain edges.
No constant folding or control-flow termination is assumed by this analysis.
"""
from .compiler import CompileError
from .scene_init import _commented, EVENT_END, EVENT_CALL_CUSTOM_EVENT


def scene_graph(project, inits, hooks=()):
    scenes = {s.scene_id: s for s in inits.scenes}
    start = project.settings['startSceneId']
    if start not in scenes:
        raise CompileError(f"settings:startSceneId {start!r}: missing or disabled scene")
    custom = {s['id']: (path, s) for path, s in project.by_type['script']}
    disabled = {s['scene'] for s in inits.excluded}
    edges, diagnostics = [], []
    visited = set()

    def visit(owner, event, where):
        if event['command'] == EVENT_END or _commented(event):
            return
        if event['command'] == 'EVENT_SWITCH_SCENE':
            target = (event.get('args') or {}).get('sceneId')
            if not isinstance(target, str) or target not in scenes:
                reason = 'disabled' if isinstance(target, str) and target in disabled else 'missing'
                diagnostics.append(f"{where}: sceneId {target!r}: {reason} scene")
            else:
                edges.append({'from': owner, 'to': target, 'path': where})
        for key, children in (event.get('children') or {}).items():
            for i, child in enumerate(children or []):
                visit(owner, child, f'{where}.children.{key}[{i}]')
        if event['command'] == EVENT_CALL_CUSTOM_EVENT:
            cid = event['args']['customEventId']
            key = (owner, cid)
            if key not in visited:
                visited.add(key)
                if cid not in custom:
                    diagnostics.append(f"{where}: customEventId {cid!r}: missing script")
                    return
                path, script = custom[cid]
                for i, child in enumerate(script['script']):
                    visit(owner, child, f'{path}:script[{i}]')

    for thread in [*inits.scenes, *hooks]:
        for event, where in zip(thread.events, thread.sources):
            visit(thread.scene_id, event, where)
    adjacency = {sid: [] for sid in scenes}
    for edge in edges:
        adjacency[edge['from']].append(edge['to'])
    reachable, pending = set(), [start]
    while pending:
        sid = pending.pop()
        if sid not in reachable:
            reachable.add(sid)
            pending.extend(adjacency[sid])
    return {'scope': 'compiled init and hook threads; explicit EVENT_SWITCH_SCENE only',
            'start': start, 'reachable': [sid for sid in scenes if sid in reachable],
            'unreachable': [sid for sid in scenes if sid not in reachable],
            'transitions': edges, 'diagnostics': diagnostics,
            'runtime_delivered': False}
