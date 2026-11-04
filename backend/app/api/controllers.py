"""Per-controller endpoints: driver detail, actions, the generic HTTP proxy and the FPP endpoint catalog."""
import json
import logging
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from ..drivers.base import HostDetail
from ..fleet import HostInfo
from .hosts import collector

log = logging.getLogger("fppcc.api")
router = APIRouter(prefix="/api")

PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
PROXY_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
# Headers passed through in each direction. Everything else (cookies, host, hop-by-hop) is dropped.
REQUEST_HEADERS = {"content-type", "accept", "accept-encoding"}
RESPONSE_HEADERS = {"content-type", "content-encoding", "content-length", "content-disposition",
                    "cache-control", "last-modified", "etag"}


def get_host(host_id: int, c=Depends(collector)) -> HostInfo:
    h = c.hosts.get(host_id)
    if h is None:
        raise HTTPException(404, "host not found")
    return h


@router.get("/hosts/{host_id}/detail", response_model=HostDetail)
async def host_detail(host: HostInfo = Depends(get_host), c=Depends(collector)):
    driver = c.drivers.get(host.kind)
    if driver is None:
        raise HTTPException(409, f"no driver for kind '{host.kind}'")
    try:
        return await driver.detail(host.address)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{host.name} did not respond: {type(e).__name__}")


class ActionResult(BaseModel):
    ok: bool
    action: str
    message: str


@router.post("/hosts/{host_id}/actions/{action}", response_model=ActionResult)
async def host_action(action: str, host: HostInfo = Depends(get_host), c=Depends(collector)):
    driver = c.drivers.get(host.kind)
    if driver is None or action not in {a.id for a in driver.actions}:
        raise HTTPException(404, f"{host.kind} controllers have no action '{action}'")
    log.info("action %s on %s (%s)", action, host.name, host.address)
    try:
        msg = await driver.run_action(host.address, action)
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"{host.name} returned HTTP {e.response.status_code}")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{host.name} did not respond: {type(e).__name__}")
    c.wake()
    return ActionResult(ok=True, action=action, message=msg)


async def _forward(request: Request, url: str, client: httpx.AsyncClient) -> StreamingResponse:
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() in REQUEST_HEADERS}
    if not body:
        # FPP's PHP API returns 404 for e.g. GET /api/files/logs when Content-Type: application/json is set
        headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    req = client.build_request(request.method, url, params=request.query_params, content=body or None,
                               headers=headers, timeout=PROXY_TIMEOUT)
    try:
        resp = await client.send(req, stream=True)
    except httpx.TimeoutException:
        raise HTTPException(504, "controller timed out")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"controller unreachable: {type(e).__name__}")
    out_headers = {k: v for k, v in resp.headers.items() if k.lower() in RESPONSE_HEADERS}
    out_headers["x-upstream-url"] = str(resp.request.url)
    return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=out_headers,
                             background=BackgroundTask(resp.aclose))


def _clean_path(path: str) -> str:
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise HTTPException(400, "path must not contain '..'")
    return "/".join(parts)


@router.api_route("/hosts/{host_id}/fpp/{path:path}", methods=PROXY_METHODS, include_in_schema=False)
async def fpp_proxy(path: str, request: Request, host: HostInfo = Depends(get_host), c=Depends(collector)):
    """Any FPP API endpoint: /api/hosts/{id}/fpp/<x> -> http://<controller>/api/<x> (method, query, body kept)."""
    if host.kind != "fpp":
        raise HTTPException(409, f"{host.name} is not an FPP controller; use /api/hosts/{host.id}/http/...")
    return await _forward(request, f"http://{host.address}/api/{_clean_path(path)}", c.client)


@router.api_route("/hosts/{host_id}/http/{path:path}", methods=PROXY_METHODS, include_in_schema=False)
async def http_proxy(path: str, request: Request, host: HostInfo = Depends(get_host), c=Depends(collector)):
    """Raw HTTP to any controller (e.g. Falcon status.xml): /api/hosts/{id}/http/<x> -> http://<controller>/<x>."""
    return await _forward(request, f"http://{host.address}/{_clean_path(path)}", c.client)


@lru_cache
def _catalog() -> dict:
    return json.loads((Path(__file__).parent.parent / "data" / "fpp_endpoints.json").read_text())


@router.get("/fpp/endpoints")
async def fpp_endpoints():
    """FPP 10.3 API routes (for the API console's autocomplete)."""
    return _catalog()
