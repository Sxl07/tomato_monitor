#!/usr/bin/env python3
"""Spec 023 preview benchmark collector — READ-ONLY evidence gathering.

This helper does NOT drive the application. The operator uses the real UI to
open the setup preview, start/finalize a monitoring, etc. This script only:

    1. queries GET /api/camera/preview-diagnostics (authenticated);
    2. reads a pipeline_metrics.json file given by argument;
    3. samples local CPU / RAM / temperature (best-effort, may be absent on PC);
    4. writes a readable JSON summary.

It NEVER: creates/starts/finalizes a monitoring, opens the camera, or performs
any business POST. Uses only the Python standard library.

Auth: the diagnostics endpoint requires a session cookie. Provide it via the
PREVIEW_BENCHMARK_COOKIE environment variable (e.g. "session=<token>"). The
cookie value is NEVER printed and NEVER written to the output.

Usage:
    python scripts/benchmarks/collect_preview_benchmark.py \
        --base-url http://localhost:8000 \
        --pipeline-metrics outputs/monitorings/42/pipeline_metrics.json \
        --output docs/benchmarks/spec023-run.json

    # cookie (not printed) from env:
    #   PREVIEW_BENCHMARK_COOKIE="session=..."   (Linux/mac)
    #   $env:PREVIEW_BENCHMARK_COOKIE="session=..."  (PowerShell)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

_COOKIE_ENV = "PREVIEW_BENCHMARK_COOKIE"


def _fetch_preview_diagnostics(base_url: str, timeout: float = 5.0):
    """GET /api/camera/preview-diagnostics. Returns dict or an error marker.

    The cookie (if any) is sent as a header and NEVER included in the result.
    """
    url = base_url.rstrip("/") + "/api/camera/preview-diagnostics"
    req = urllib.request.Request(url, method="GET")
    cookie = os.environ.get(_COOKIE_ENV, "").strip()
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
            return {"ok": True, "status": resp.status, "data": json.loads(body)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": "http_error"}
    except Exception as e:  # connectivity / parse errors are non-fatal
        return {"ok": False, "error": type(e).__name__, "message": str(e)}


def _read_pipeline_metrics(path: str):
    if not path:
        return {"ok": False, "error": "no_path"}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return {"ok": True, "data": json.load(fh)}
    except FileNotFoundError:
        return {"ok": False, "error": "not_found", "path": path}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "message": str(e)}


def _sample_system():
    """Best-effort CPU / RAM / temperature. Missing values -> None (e.g. on PC)."""
    sample = {"cpu_percent": None, "ram_used_mb": None, "temperature_c": None}
    # CPU/RAM via psutil if available (optional dependency; do not require it).
    try:
        import psutil  # type: ignore

        sample["cpu_percent"] = psutil.cpu_percent(interval=0.2)
        sample["ram_used_mb"] = round(psutil.virtual_memory().used / (1024 * 1024), 1)
    except Exception:
        pass
    # Temperature: Raspberry Pi thermal zone (absent on PC -> stays None).
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as fh:
            milli = int(fh.read().strip())
            sample["temperature_c"] = round(milli / 1000.0, 1)
    except Exception:
        pass
    return sample


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Spec 023 preview benchmark collector (read-only)."
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--pipeline-metrics",
        default="",
        help="Path to a pipeline_metrics.json produced by a real monitoring.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Where to write the JSON summary (default: stdout).",
    )
    args = parser.parse_args(argv)

    summary = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "preview_diagnostics": _fetch_preview_diagnostics(args.base_url),
        "pipeline_metrics": _read_pipeline_metrics(args.pipeline_metrics),
        "system": _sample_system(),
        "note": (
            "Read-only collector. Physical validation on Raspberry Pi is the "
            "source of truth; these numbers are supporting evidence."
        ),
    }

    rendered = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(rendered + "\n")
        print(f"Summary written to {args.output}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
