"""Source clients (T07): the one place where a real integration replaces the mock.

The orchestrator (`enrichment.py`) needs a `SourceClient`: `fetch(source, inn)` returns
the source's payload or raises `SourceUnavailable`, `SourceTimeout` or `SourceNotFound`;
a source that hangs is cut off by the orchestrator's own timeout.

- `MockSourceService` (T06) plays every source in process and is the default.
- `HttpSourceClient` calls the same service over HTTP (`router.py`), the way an adapter
  calls a real integration.
"""
from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import BaseModel

from apps.api.contracts import (AccountsPayload, AmlPayload, ArbitrationPayload, BkiPayload, ClientCardPayload,
                                EgrulPayload, FnsPayload, FsspPayload, SourceId)

from .service import SourceNotFound, SourceTimeout, SourceUnavailable

PAYLOADS: dict[SourceId, type[BaseModel]] = {
    SourceId.ACCOUNTS: AccountsPayload, SourceId.CLIENT_CARD: ClientCardPayload, SourceId.EGRUL: EgrulPayload,
    SourceId.FNS: FnsPayload, SourceId.BKI: BkiPayload, SourceId.FSSP: FsspPayload,
    SourceId.ARBITRATION: ArbitrationPayload, SourceId.AML: AmlPayload,
}


class SourceClient(Protocol):
    async def fetch(self, source: SourceId, inn: str) -> BaseModel: ...


class HttpSourceClient:
    """Calls GET {base_url}/mock-sources/{source}/{inn} and validates the payload against its contract."""

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip('/')
        self.transport = transport

    async def fetch(self, source: SourceId, inn: str) -> BaseModel:
        source = SourceId(source)
        # No client timeout here: the orchestrator decides how long to wait.
        async with httpx.AsyncClient(transport=self.transport, timeout=None) as client:
            try:
                response = await client.get(f'{self.base_url}/mock-sources/{source.value}/{inn}')
            except httpx.TransportError as e:
                raise SourceUnavailable(source, inn, f'{source.value}: {e.__class__.__name__}') from e
        if response.status_code == 404:
            raise SourceNotFound(source, inn, _detail(response))
        if response.status_code == 504:
            raise SourceTimeout(source, inn, _detail(response))
        if response.status_code != 200:
            raise SourceUnavailable(source, inn, _detail(response))
        return PAYLOADS[source].model_validate(response.json())


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get('detail', response.text))
    except ValueError:
        return response.text or f'HTTP {response.status_code}'
