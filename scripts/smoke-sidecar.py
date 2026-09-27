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
        timeout=90,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    if len(responses) != len(requests):
        raise RuntimeError("Unexpected response count from frozen core")
    for request, response in zip(requests, responses, strict=True):
        if response.get("id") != request["id"] or "error" in response:
            raise RuntimeError("Frozen core RPC validation failed")
    dashboard = responses[1]["result"]
    chromium = responses[2]["result"]
    if not chromium.get("ok") or not chromium.get("version"):
        raise RuntimeError("Bundled Chromium did not start")
    print(f"Frozen core OK: {dashboard['total']} profiles, Chromium {chromium['version']}")


if __name__ == "__main__":
    main()
