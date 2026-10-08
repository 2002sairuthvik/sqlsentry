from __future__ import annotations

from fastapi import APIRouter, Depends

from ...engine import SQLSentry
from ...schema.models import SchemaCatalog
from ..auth import Authenticator, Principal
from ..deps import principal, sentry
from ..schemas import DatasourceInfo

router = APIRouter(prefix="/v1/datasources", tags=["datasources"])


@router.get("", response_model=list[DatasourceInfo])
def list_datasources(
    p: Principal = Depends(principal), s: SQLSentry = Depends(sentry)
) -> list[DatasourceInfo]:
    out = []
    for name in s.datasources():
        scopes = p.scopes_for(name)
        if scopes:
            ds = s.datasource(name)
            out.append(
                DatasourceInfo(
                    id=name,
                    description=ds.description,
                    dialect=ds.resolved_dialect,
                    mode=ds.mode,
                    can_execute=ds.can_connect and ds.policy.allow_execute,
                    scopes=sorted(scopes),
                )
            )
    return out


@router.get("/{datasource}/schema", response_model=SchemaCatalog)
def get_schema(
    datasource: str, p: Principal = Depends(principal), s: SQLSentry = Depends(sentry)
) -> SchemaCatalog:
    Authenticator.require(p, datasource, "schema")
    return s.schema(datasource)
