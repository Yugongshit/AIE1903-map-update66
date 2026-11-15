"""Spectator diagnostics from current physics only; never Planning inputs."""


class TrafficWaits:
    def __init__(self):
        self.history = {}
        self.rows = {}

    def update(self, actors, reasons, diagnostics, braking, junctions, time_s, identities):
        rows = {}
        for actor in actors:
            key = actor['vehicle_id']
            if key not in identities:
                continue
            identity = identities[key]
            stopped = actor['speed_m_s'] < .3
            previous = self.history.get(key, {})
            since = previous.get('since', time_s) if previous.get('identity') == identity else time_s
            if not stopped:
                since = time_s
            route = diagnostics.get(key, {}) or {}
            leader = (route.get('following') or {}).get('leader')
            reason = reasons.get(key)
            if stopped and not reason:
                reason = 'following_queue' if leader else 'stopped_unclassified'
                if not leader and time_s-since >= 3 and route.get('status') == 'tracking' and (route.get('target_speed_m_s') or 0) > .3:
                    reason = 'tracking_without_progress'
            waiting = set()
            if braking.get(key):
                waiting.add(braking[key]['vehicle_id'])
            if stopped and leader:
                waiting.add(leader['vehicle_id'])
            zone = junctions.get(key, {})
            waiting.update(zone.get('waiting_for', []))
            waiting.discard(key)
            # Keep the most recent explicit stop over short accelerator/brake
            # oscillations, with an age. A single sampled accelerator frame must
            # not erase the obstacle that stopped this same actor 0.1 s ago.
            recent = previous.get('recent') if previous.get('identity') == identity else None
            if reasons.get(key):
                recent = dict(reason=reasons[key], at=time_s, waiting_for=sorted(waiting),
                              junction_id=zone.get('junction_id'),
                              waiting_identities={v: identities.get(v) for v in waiting})
            if recent and time_s-recent['at'] > 1.:
                recent = None
            if stopped and not reasons.get(key) and recent:
                reason = recent['reason']
                waiting.update(v for v in recent['waiting_for'] if identities.get(v) == recent['waiting_identities'].get(v))
            self.history[key] = dict(identity=identity, since=since, recent=recent)
            rows[key] = dict(reason=reason, stopped=stopped,
                stopped_for_s=round(max(0., time_s-since), 2) if stopped else 0.,
                waiting_for=sorted(waiting), junction_id=zone.get('junction_id'),
                recent_stop_reason=recent['reason'] if recent else None,
                recent_stop_age_s=round(time_s-recent['at'], 2) if recent else None,
                actor_instance_id=identity, synthetic_diagnostic=True,
                controller_status=route.get('status'), controller_reason=route.get('reason'),
                lateral_error_m=route.get('lateral_error_m'),
                heading_error_rad=route.get('heading_error_rad'), tracking_failure=route.get('tracking_failure'),
                target_speed_m_s=route.get('target_speed_m_s'))
        self.rows = rows
        return rows

    def report(self, pedestrians=()):
        # Strongly connected wait groups, not proof that physics cannot recover.
        nodes = dict(self.rows)
        nodes.update({p['event_id']: dict(stopped=p['stationary_for_s'] >= .5,
                     waiting_for=p['waiting_for']) for p in pedestrians})
        graph = {k: {v for v in r['waiting_for'] if v in nodes and nodes[v]['stopped']}
                 for k, r in nodes.items() if r['stopped']}
        def reachable(start):
            seen, todo = set(), [start]
            while todo:
                node = todo.pop()
                if node in seen:
                    continue
                seen.add(node)
                todo.extend(graph.get(node, set()) - seen)
            return seen
        reach = {k: reachable(k) for k in graph}
        groups = {tuple(sorted(v for v in reach[k] if k in reach.get(v, set()))) for k in graph}
        return dict(scope='spectator_current_simulation_only',
                    moving=sum(not r['stopped'] for k, r in self.rows.items() if k != 'ego'),
                    waiting_cycles=[list(g) for g in sorted(groups) if len(g) > 1],
                    stopped_over_30s=[k for k, r in self.rows.items() if k != 'ego' and r['stopped_for_s'] >= 30])
