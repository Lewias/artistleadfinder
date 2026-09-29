"""Verify the frozen core's real stdio transport without a local Python runtime."""

import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    executable = Path(sys.argv[1]).resolve(strict=True)
    requests = [
        {"id": index, "method": method, "params": {}}
        for index, method in enumerate(
            ["system.info", "dashboard.get", "browser.runtime.self_test", "system.shutdown"], start=1
        )
    ]
    result = subprocess.run(
        [str(executable)],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        # macOS downloads Chromium on the first self-test.
        timeout=1200,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    failed = [
        response
        for request, response in zip(requests, responses)
        if response.get("id") != request["id"] or "error" in response
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
    dashboard = responses[1]["result"]
    chromium = responses[2]["result"]
    if not chromium.get("ok") or not chromium.get("version"):
        raise RuntimeError("Bundled Chromium did not start")
    print(f"Frozen core OK: {dashboard['total']} profiles, Chromium {chromium['version']}")


if __name__ == "__main__":
    main()
