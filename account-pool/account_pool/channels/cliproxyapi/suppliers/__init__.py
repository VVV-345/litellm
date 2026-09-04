"""本包包含 CLIProxyAPI 的供应商静态契约。"""

from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry

__all__ = ("SupplierDefinition", "SupplierRegistry")
