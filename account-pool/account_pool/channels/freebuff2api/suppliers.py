"""本模块声明 FreeBuff2API 渠道的静态供应商白名单。"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.channels.freebuff2api.channel import freebuff_supplier
from account_pool.domain import SupplierKind


def freebuff_suppliers() -> Mapping[SupplierKind, SupplierDefinition]:
    return MappingProxyType({SupplierKind.FREEBUFF: freebuff_supplier()})


FREEBUFF_SUPPLIERS: Final = freebuff_suppliers()

__all__ = ("FREEBUFF_SUPPLIERS", "freebuff_suppliers")
