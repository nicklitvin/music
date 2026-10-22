"""OMR on AWS Lambda: one invocation per page.

oemer is the only part of the backend that needs real CPU -- minutes of
it per page -- and it needs it in bursts, a few times a day. Sized for
those bursts, the always-on server sat idle almost all the time; on
Lambda it is paid for by the second while a page runs, and every page of a
sheet runs at once, so a 10-page sheet takes about as long as one page.

Both ends live here:

  * `handler` is the Lambda function (packaged by backend/lambda/Dockerfile).
    It runs exactly the code the server would (oemer_engine.extract_page),
    on a page PNG sent in the request.
  * `extract_remote` is what omr.process_pdf calls per page instead of
    running oemer itself, when OMR_LAMBDA_FUNCTION is set.

Pages go in the invocation payload and results come back in the response;
nothing is staged in S3 or anywhere else, so the zero-server-storage rule
holds -- the Lambda writes the page to a TemporaryDirectory for oemer for
the length of that page, exactly as the server does, and nothing else.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from typing import Any

from app.config import settings
from app.services import omr_log

# Lambda refuses synchronous payloads over 6 MB each way. A page rendered at
# 300 dpi is 0.4-0.8 MB of PNG (1.1 MB as base64), so this only trips on
# something like a full-colour photo scan.
MAX_PAYLOAD_BYTES = 6 * 1024 * 1024

# Longer than any page takes, and than Lambda's own 15-minute ceiling, so a
# slow page fails on the Lambda side with a real error rather than here.
READ_TIMEOUT_SECONDS = 960


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Lambda entry point. `{"ping": true}` imports and loads everything
    without running a page, which is how a deploy checks the image works."""
    from app.services import oemer_engine

    omr_log.configure()
    if event.get("ping"):
        oemer_engine.prepare_models()
        return {"ok": True, "cpus": os.cpu_count()}

    started = time.perf_counter()
    boxes, music_xml = oemer_engine.extract_page(
        base64.b64decode(event["png_base64"]),
        event["page_index"],
        event["width"],
        event["height"],
    )
    return {
        "page_index": event["page_index"],
        "boxes": [box.model_dump() for box in boxes],
        "music_xml": music_xml,
        "seconds": round(time.perf_counter() - started, 1),
        "cpus": os.cpu_count(),
    }


_client = None
_client_lock = threading.Lock()


def _lambda_client():
    global _client
    with _client_lock:
        if _client is None:
            import boto3
            from botocore.config import Config

            _client = boto3.client(
                "lambda",
                region_name=settings.omr_lambda_region or None,
                config=Config(
                    connect_timeout=10,
                    read_timeout=READ_TIMEOUT_SECONDS,
                    # Retries throttling (more pages at once than the
                    # account's concurrency allows) with backoff.
                    retries={"mode": "standard", "max_attempts": 5},
                    max_pool_connections=max(10, settings.omr_lambda_concurrency),
                ),
            )
        return _client


def extract_remote(item: tuple[int, bytes, int, int], client=None) -> tuple[int, list[dict], str]:
    """Same contract as omr._extract_one, with the page run on Lambda."""
    index, png_bytes, width, height = item
    payload = json.dumps(
        {
            "png_base64": base64.b64encode(png_bytes).decode("ascii"),
            "page_index": index,
            "width": width,
            "height": height,
        }
    ).encode()
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"Page {index + 1} is too large to send for recognition "
            f"({len(payload) / 1024**2:.1f} MB; the limit is 6 MB)"
        )

    response = (client or _lambda_client()).invoke(
        FunctionName=settings.omr_lambda_function,
        InvocationType="RequestResponse",
        Payload=payload,
    )
    body = json.loads(response["Payload"].read() or b"null")
    if response.get("FunctionError"):
        error = body if isinstance(body, dict) else {}
        raise RuntimeError(
            f"Recognition failed on page {index + 1}: "
            f"{error.get('errorType', 'error')}: {error.get('errorMessage', body)}"
        )
    omr_log.logger.info(
        "page %d: %.0fs on Lambda (%s vCPUs), %d notes",
        index + 1,
        body.get("seconds", 0),
        body.get("cpus", "?"),
        len(body["boxes"]),
    )
    return body["page_index"], body["boxes"], body["music_xml"]
