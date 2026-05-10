"""Settings API — application configuration management.

Endpoints are split by HTTP verb across sub-modules:
- ``_get.py``:       GET  /settings            (current config)
- ``_update.py``:    PUT  /settings, POST /settings/reload-config
- ``_bindings.py``:  GET  /settings/bindings   (available providers)
- ``_account.py``:   DELETE /settings/account  (GDPR erasure)
- ``_common.py``:    shared state, router instance, invariants
- ``_rebuild.py``:   POST /settings/rebuild-vectors
"""

from . import _account, _bindings, _get, _rebuild, _update  # noqa: F401
from ._common import router
from ._get import _get_current_settings  # noqa: F401
from ._update import _update_settings_in_memory  # noqa: F401

__all__ = ["router"]
