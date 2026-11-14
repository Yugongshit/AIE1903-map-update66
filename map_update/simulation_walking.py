"""Seeded walking trips on source walkways and crosswalks, not pedestrian intent inference.

The 1 m navigation lattice is eroded for a 0.6 m square body. A bounded
0.8 m crosswalk curb tolerance bridges sub-metre gaps in the source annotation.
No links are invented between disconnected sidewalks. Goals remain private.
"""
from functools import lru_cache
import heapq
import json
import math
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union


@lru_cache(maxsize=4)
def _source_geometry(source_file):
    raw=json.loads(Path(source_file).read_text())
    nodes={n['token']:(n['x'],n['y']) for n in raw['node']}
    polygons={p['token']:Polygon([nodes[n] for n in p['exterior_node_tokens']],
        [[nodes[n] for n in h['node_tokens']] for h in p.get('holes',[])]) for p in raw['polygon']}
    return ([polygons[r['polygon_token']] for r in raw['walkway']],
            {r['token']:polygons[r['polygon_token']] for r in raw['ped_crossing']})


def walkway_display(features, source_file):
    """Original-map surface overlay, excluded from structural writes and planner exports."""
    walkways,crossings=_source_geometry(str(source_file))
    scene_crossings={k:Polygon(v['polygon']) for k,v in features.items() if v.get('type')=='CROSSWALK'}
    if not scene_crossings or any(k not in crossings or p.hausdorff_distance(crossings[k])>.01 for k,p in scene_crossings.items()):return []
    roads=[LineString(f['polyline']) for f in features.values() if f.get('type','').startswith('LANE') and len(f.get('polyline',[]))>1]
    if not roads:return []
    x0,y0,x1,y1=unary_union(roads).bounds;region=box(x0-45,y0-45,x1+45,y1+45)
    rows=[]
    for poly in walkways:
        if not poly.intersects(region):continue
        clipped=poly.intersection(region)
        for part in ([clipped] if clipped.geom_type=='Polygon' else getattr(clipped,'geoms',[])):
            if part.geom_type!='Polygon':continue
            rows.append(dict(polygon=[list(p) for p in part.exterior.coords],holes=[[list(p) for p in ring.coords] for ring in part.interiors]))
    return rows


class WalkingNetwork:
    def __init__(self, features, paths, obstacles=(), source_file=None, expanded=False):
        source_file = source_file or Path(__file__).resolve().parents[1] / 'AIE1903 原始资料/maps/expansion/singapore-onenorth.json'
        source_walkways,source_crossings=_source_geometry(str(source_file))
        self.crosswalks = {k: Polygon(v['polygon']) for k, v in features.items() if v.get('type') == 'CROSSWALK'}
        if not self.crosswalks or any(k not in source_crossings or p.hausdorff_distance(source_crossings[k]) > .01 for k, p in self.crosswalks.items()):
            raise ValueError('Pedestrian source map does not match scene crosswalk coordinates')
        self.expanded=expanded
        if expanded:
            paths=[f['polyline'] for f in features.values() if f.get('type','').startswith('LANE') and len(f.get('polyline',[]))>1]
        roads = unary_union([LineString(p) for p in paths])
        x0, y0, x1, y1 = roads.bounds
        region = box(x0-45, y0-45, x1+45, y1+45)
        walkways = [poly for poly in source_walkways if poly.intersects(region)]
        self.sidewalk = unary_union(walkways).intersection(region)
        self.crosswalks = {k: p for k, p in self.crosswalks.items() if p.intersects(region)}
        self.surface = unary_union([self.sidewalk, *[p.buffer(.8) for p in self.crosswalks.values()]]).intersection(region)
        self.allowed = self.surface.buffer(-.45)
        if obstacles:
            self.allowed = self.allowed.difference(unary_union(obstacles).buffer(.45))
        self.motion_area = self.allowed.buffer(.08)
        shapely.prepare(self.motion_area)
        # Vectorised geometry builds the immutable graph once per room/reset.
        xs = np.arange(math.ceil(x0-45), math.floor(x1+45)+1)
        ys = np.arange(math.ceil(y0-45), math.floor(y1+45)+1)
        grid = np.stack(np.meshgrid(xs, ys), axis=-1).reshape(-1, 2)
        self.points = grid[shapely.covers(self.allowed, shapely.points(grid))].astype(float)
        index = {tuple(p): i for i, p in enumerate(self.points)}
        pairs = [(i, index[(x+dx, y+dy)]) for i, (x, y) in enumerate(self.points)
                 for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)) if (x+dx, y+dy) in index]
        self.edges = [[] for _ in self.points]
        if pairs:
            pair_array = np.array(pairs)
            valid = shapely.covers(self.allowed, shapely.linestrings(self.points[pair_array]))
            for (a, b), ok in zip(pairs, valid):
                if ok:
                    distance = math.dist(self.points[a], self.points[b])
                    self.edges[a].append((b, distance)); self.edges[b].append((a, distance))
        self.component = {}; self.components = []
        for start in range(len(self.points)):
            if start in self.component: continue
            number = len(self.components); stack = [start]; group = []
            self.component[start] = number
            while stack:
                node = stack.pop(); group.append(node)
                for neighbour, _ in self.edges[node]:
                    if neighbour not in self.component:
                        self.component[neighbour] = number; stack.append(neighbour)
            self.components.append(sorted(group))
        # Destinations represent sampled sidewalk locations, not invented shops.
        crossings = unary_union(list(self.crosswalks.values())).buffer(1.)
        candidates = shapely.covers(self.sidewalk.buffer(-.45), shapely.points(self.points)) & ~shapely.intersects(crossings, shapely.points(self.points))
        self.destinations = {i: [n for n in group if candidates[n]] for i, group in enumerate(self.components)}
        self.destinations = {i: ns for i, ns in self.destinations.items() if len(ns) >= 50 and max(np.ptp(self.points[ns], axis=0)) >= 60}
        # Prefer the connected district with multiple crossing choices. Disconnected
        # annotations remain disconnected; do not bridge streets to add variety.
        connected = {}
        for i, ns in self.destinations.items():
            pts = shapely.points(self.points[self.components[i]])
            count = sum(bool(shapely.intersects(poly, pts).any()) for poly in self.crosswalks.values())
            if count >= 2: connected[i] = ns
        if connected and not expanded: self.destinations = connected
        self.crossing_ids = list(self.crosswalks)
        self.crossing_shapes = list(self.crosswalks.values())
        self.crossing_buffers = {k: p.buffer(.65) for k, p in self.crosswalks.items()}

    def spawn_nodes(self, count, rng, occupied):
        candidates = [n for group in self.destinations.values() for n in group]
        rng.shuffle(candidates)
        if self.expanded:
            groups={}
            for n in candidates:
                x,y=self.points[n];groups.setdefault((int(x//80),int(y//80)),[]).append(n)
            keys=sorted(groups);rng.shuffle(keys)
            candidates=[groups[k][i] for i in range(max(map(len,groups.values()),default=0)) for k in keys if i<len(groups[k])]
        chosen = []
        for node in candidates:
            p = Point(self.points[node])
            if any(p.distance(o) < 1. for o in occupied): continue
            if any(math.dist(self.points[node], self.points[other]) < (8. if self.expanded else 12.) for other in chosen): continue
            chosen.append(node)
            if len(chosen) == count: break
        return chosen

    def trip(self, start, rng, recent=(), distance_range=(35,180)):
        candidates = [n for n in self.destinations[self.component[start]]
                      if distance_range[0] <= math.dist(self.points[start], self.points[n]) <= distance_range[1]
                      and all(math.dist(self.points[n], self.points[old]) > 20 for old in recent[-3:])]
        if not candidates:
            candidates = [n for n in self.destinations[self.component[start]] if math.dist(self.points[start], self.points[n]) >= 20]
        if not candidates: return [start]
        goal = rng.choice(candidates)
        # Per-trip direction preference changes costs mildly without random turns.
        preference = rng.uniform(0, math.tau)
        frontier = [(0., start)]; costs = {start: 0.}; parents = {}
        while frontier:
            _, node = heapq.heappop(frontier)
            if node == goal: break
            for neighbour, distance in self.edges[node]:
                delta = self.points[neighbour]-self.points[node]
                cost = costs[node]+distance*(1.+.12*abs(math.sin(math.atan2(delta[1], delta[0])-preference)))
                if cost < costs.get(neighbour, math.inf):
                    costs[neighbour] = cost; parents[neighbour] = node
                    heapq.heappush(frontier, (cost+math.dist(self.points[neighbour], self.points[goal]), neighbour))
        route = [goal]
        while route[-1] != start: route.append(parents[route[-1]])
        route.reverse()
        # Keep turns and crosswalk approach points; only remove collinear nodes.
        result = [route[0]]
        for i in range(1, len(route)-1):
            a, b, c = (self.points[n] for n in route[i-1:i+2])
            if not np.array_equal(b-a, c-b) or any(p.distance(Point(b)) < 2 for p in self.crossing_shapes): result.append(route[i])
        result.append(goal)
        return result

    def crossing_at(self, position):
        point = Point(position)
        return next((k for k, p in self.crossing_buffers.items() if p.covers(point)), None)

    def reroute(self, position, goal, rng, blocked, recent=(), avoid_direction=None):
        """Reconnect to the lattice without crossing a current blocked footprint.

        Keep the destination if reachable; otherwise select a new reachable
        sidewalk destination. A cancelled trip is not counted as an arrival.
        """
        valid = ~shapely.intersects(blocked, shapely.points(self.points))
        nearby = sorted((math.dist(position, p), i) for i, p in enumerate(self.points) if valid[i] and math.dist(position, p) < 3.)
        start = next((i for _, i in nearby if self.motion_area.covers(LineString([position, self.points[i]])) and not blocked.intersects(LineString([position, self.points[i]]))), None)
        if start is None: return None
        parents = {}; costs = {start: 0.}; queue = [(0., start)]
        while queue:
            cost, node = heapq.heappop(queue)
            if cost != costs[node]: continue
            for other, length in self.edges[node]:
                if not valid[other] or blocked.intersects(LineString([self.points[node], self.points[other]])): continue
                candidate = cost+length
                if candidate < costs.get(other, math.inf):
                    parents[other] = node; costs[other] = candidate; heapq.heappush(queue, (candidate, other))
        if goal not in costs or avoid_direction is not None:
            choices = [n for n in self.destinations.get(self.component[start], []) if n in costs
                       and costs[n] >= 25 and all(math.dist(self.points[n], self.points[old]) > 15 for old in recent[-2:])]
            if avoid_direction is not None:
                choices=[n for n in self.destinations.get(self.component[start],[]) if n in costs and costs[n]>=25
                         and sum((self.points[n][i]-position[i])*avoid_direction[i] for i in (0,1)) < -10]
            if not choices: return None
            goal = rng.choice(choices)
        route = [goal]
        while route[-1] != start: route.append(parents[route[-1]])
        return list(reversed(route))
