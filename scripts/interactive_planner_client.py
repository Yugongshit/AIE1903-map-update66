"""Connect a local planner to an interactive room through JSON observations/actions.

The included plan() is deliberately a brake-only integration hook. Replace it
with your own planner locally; the server never downloads or executes this file.
"""
from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


VEHICLES = ("ego", "P1", "P2", "P3", "P4", "P5")


class APIError(RuntimeError):
    pass


def plan(observation, vehicle_id, map_context):
    """Replace this function with your local planner.

    observation is a current physical-state snapshot, map_context is the
    versioned road geometry and declared static-world prior fetched once.
    Return normalized [steering, throttle/brake], each within [-1, 1].
    No recorded future vehicle trajectory is supplied by this interface.
    """
    return [0.0, -1.0]


class RoomClient:
    def __init__(self, base_url, timeout_s=2.0):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError("Use an HTTP(S) server address without credentials, query or fragment")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def request(self, path, *, query=None, body=None):
        url = self.base_url + "/api/interactive/" + path
        if query:
            url += "?" + urlencode(query)
        headers = {"Accept": "application/json", "Cache-Control": "no-store"}
        data = None
        if body is not None:
            data = json.dumps(body, allow_nan=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                payload = json.load(response)
        except HTTPError as error:
            # Do not echo a response body that might contain submitted tickets.
            raise APIError(f"HTTP {error.code}: {path} request rejected") from None
        except (URLError, OSError, ValueError) as error:
            raise APIError(f"{path} request failed ({type(error).__name__})") from None
        if not isinstance(payload, dict):
            raise APIError(f"{path} did not return a JSON object")
        return payload


def checked_action(action):
    if not isinstance(action, (list, tuple)) or len(action) != 2:
        raise ValueError("plan() must return [steering, throttle/brake]")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or not -1 <= value <= 1 for value in action):
        raise ValueError("plan() actions must contain two finite numbers within [-1, 1]")
    return [float(value) for value in action]


def run(client, session_id, vehicle_ids, join_token, *, duration_s=None, poll_interval_s=0.05):
    state = client.request("state").get("room")
    if not isinstance(state, dict) or state.get("session_id") != session_id:
        raise APIError("The requested session is not the current interactive room")
    epoch = state.get("epoch")
    if type(epoch) is not int:
        raise APIError("Room state is missing its generation epoch")
    identity = {"session_id": session_id, "epoch": epoch}
    map_context = client.request("map", query={"session_id": session_id})
    claim = client.request("claim", body={**identity, "join_token": join_token,
        "vehicle_ids": vehicle_ids, "kind": "external", "client_label": "local-planner-example"})
    token = claim.get("lease_token", claim.get("token"))
    if not isinstance(token, str) or not token:
        raise APIError("Claim response is missing its vehicle lease token")
    credentials = {**identity, "lease_token": token}
    print("External control claimed: " + ", ".join(vehicle_ids), flush=True)
    print("The supplied plan() sends braking only. Replace it with your local planner.", flush=True)
    started, last_heartbeat, previous_step, sequence = time.monotonic(), 0.0, None, 0
    try:
        while duration_s is None or time.monotonic() - started < duration_s:
            now = time.monotonic()
            if now - last_heartbeat >= 0.75:
                client.request("heartbeat", body=credentials)
                last_heartbeat = time.monotonic()
            observation = client.request("observation", query={
                "session_id": session_id, "vehicle_id": vehicle_ids[0]})
            if observation.get("session_id") != session_id or observation.get("epoch") != epoch:
                raise APIError("Session or epoch changed; stop and claim the new room explicitly")
            step = observation.get("step")
            if type(step) is not int or step < 0:
                raise APIError("Observation is missing a valid simulation step")
            if previous_step is not None and step < previous_step:
                raise APIError("Observation clock rewound; stop and obtain a fresh lease")
            # A paused room returns a freshly read observation at the same
            # physical step. Keep the authorized command stream alive with a
            # new sequence; heartbeat alone does not refresh an old action.
            actions = {identifier: checked_action(plan(observation, identifier, map_context))
                       for identifier in vehicle_ids}
            sequence += 1
            client.request("input", body={**credentials, "seq": sequence,
                "observation_step": step, "actions": actions})
            previous_step = step
            time.sleep(poll_interval_s)
    finally:
        try:
            client.request("release", body=credentials)
        except APIError:
            # The server's own deadman/lease expiry handles a lost connection.
            print("Release could not be confirmed; server timeout rules apply.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8091", help="Platform server address")
    parser.add_argument("--session-id", required=True, help="Current room session ID")
    parser.add_argument("--vehicles", nargs="+", choices=VEHICLES, default=["ego"])
    parser.add_argument("--seconds", type=float, default=None, help="Optional duration before releasing control")
    parser.add_argument("--poll-interval", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()
    if len(set(args.vehicles)) != len(args.vehicles):
        parser.error("--vehicles must not contain duplicates")
    for name, value in (("--seconds", args.seconds), ("--poll-interval", args.poll_interval), ("--timeout", args.timeout)):
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error(name + " must be finite and positive")
    join_token = os.environ.get("INTERACTIVE_JOIN_TOKEN")
    if not join_token:
        try:
            join_token = getpass.getpass("Room join ticket (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            parser.exit(2, "A room join ticket is required.\n")
    if not join_token:
        parser.exit(2, "A room join ticket is required.\n")
    try:
        run(RoomClient(args.url, args.timeout), args.session_id, args.vehicles, join_token,
            duration_s=args.seconds, poll_interval_s=args.poll_interval)
    except KeyboardInterrupt:
        print("Planner client stopped; control released when the connection allowed.")
    except (APIError, ValueError) as error:
        parser.exit(1, str(error) + ". The client does not retry an uncertain control action.\n")


if __name__ == "__main__":
    main()
