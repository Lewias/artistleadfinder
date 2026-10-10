"""Entry point of the cloud parser container: `python -m cloud_worker`.

The database comes from the libpq environment (PGHOST, PGPORT, PGUSER, PGPASSWORD,
PGDATABASE); users' folders live under CLOUD_DATA (a volume); the page scripts of the
desktop shell are under CLOUD_SCRIPTS.
"""

import logging
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .driver import Driver, load_scripts
from .engine import Engine
from .runner import Runner


class _Extra(logging.Formatter):
    """Message plus the structured fields; never the environment, cookies or a request."""

    def format(self, record):
        base = super().format(record)
        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key in ("job", "stage", "error_type")
        }
        return f"{base} {extra}" if extra else base


def main() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Extra("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    data = Path(os.environ.get("CLOUD_DATA", "/data"))
    scripts = load_scripts(Path(os.environ.get("CLOUD_SCRIPTS", "/app/scripts")))
    runner = Runner(
        lambda: psycopg.connect("", autocommit=True, row_factory=dict_row),
        lambda owner: Engine(data, owner),
        lambda engine: Driver(engine.call, scripts),
        worker_id=os.environ.get("HOSTNAME", "cloud-parser"),
    )
    runner.run_forever()


if __name__ == "__main__":
    main()
