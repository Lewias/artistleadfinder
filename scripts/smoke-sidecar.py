"""Verify the frozen core's real stdio transport without a local Python runtime."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    executable = Path(sys.argv[1]).resolve(strict=True)
    requests = [
        {"id": index, "method": method, "params": {}}
        for index, method in enumerate(
            [
                "system.info",
                "dashboard.get",
                "browser.runtime.self_test",
                "system.shutdown",
            ],
            start=1,
        )
    ]
    # A fresh data folder: the check never sees the real account, leads or sessions.
    data = tempfile.mkdtemp(prefix="alf-smoke-")
    result = subprocess.run(
        [str(executable)],
        env={**os.environ, "ARTIST_LEAD_FINDER_DATA": data},
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        # macOS downloads Chromium on the first self-test.
        timeout=1200,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    responses = [
        json.loads(line) for line in result.stdout.splitlines() if line.strip()
    ]
    # With accounts on, the local data opens only after sign-in: the dashboard must then
    # answer with the sign-in requirement, not with a crash.
    accounts = (responses[0].get("result") or {}).get("account", {}).get("configured")
    locked = (
        accounts and "Войдите" in str(responses[1].get("error", ""))
        if responses[1:]
        else False
    )
    failed = [
        response
        for request, response in zip(requests, responses)
        if response.get("id") != request["id"]
        or (
            "error" in response
            and not (locked and request["method"] == "dashboard.get")
        )
    ]
    if result.returncode or len(responses) != len(requests) or failed:
        # The smoke runs on a clean profile (no sessions or keys), so the core's own
        # stderr is safe to show and is the only clue on a CI runner.
        print(result.stderr[-4000:], file=sys.stderr)
        for response in failed:
            print(f"RPC {response.get('id')}: {response.get('error')}", file=sys.stderr)
        raise RuntimeError(
            f"Frozen core failed: exit {result.returncode}, {len(responses)} responses"
        )
    if not (responses[0].get("result") or {}).get("assistant_sdk"):
        raise RuntimeError("The Anthropic SDK of the assistant is missing from the build")
    chromium = responses[2]["result"]
    if not chromium.get("ok") or not chromium.get("version"):
        raise RuntimeError("Bundled Chromium did not start")
    summary = (
        "locked until sign-in"
        if locked
        else f"{responses[1]['result']['total']} profiles"
    )
    print(f"Frozen core OK: {summary}, Chromium {chromium['version']}")


if __name__ == "__main__":
    main()
