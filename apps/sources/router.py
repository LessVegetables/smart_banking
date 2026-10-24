"""HTTP face of the mock source service: GET /mock-sources/{source}/{inn}.

Not mounted anywhere yet: the adapters (T07) decide whether they call the service in
process or over HTTP, like a real integration. To mount it:
`app.include_router(make_router(service_from_env))`.

200 is the payload; 404 an unknown INN; 503 an error; 504 a timeout, after the hang.
"""
from __future__ import annotations

from collections.abc import Callable
from functools import cache

from fastapi import APIRouter, HTTPException

from apps.api.contracts import SourceId

from .service import SOURCES, MockSourceService, SourceNotFound, SourceTimeout, SourceUnavailable


def make_router(service: Callable[[], MockSourceService]) -> APIRouter:
    """`service` is called on the first request, so the world is built only when needed."""
    get_service = cache(service)
    router = APIRouter(prefix='/mock-sources', tags=['mock-sources'])

    @router.get('/{source}/{inn}')
    async def fetch(source: SourceId, inn: str):
        if source not in SOURCES:
            raise HTTPException(404, f'{source.value} is not a source this service plays')
        try:
            payload = await get_service().fetch(source, inn)
        except SourceNotFound as e:
            raise HTTPException(404, str(e)) from e
        except SourceUnavailable as e:
            raise HTTPException(503, str(e)) from e
        except SourceTimeout as e:
            raise HTTPException(504, str(e)) from e
        return payload.model_dump(mode='json')

    return router
