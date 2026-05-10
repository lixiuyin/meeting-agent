"""Meeting management API - upload, list, get, delete"""

# Import submodules to register endpoints on the shared router
from . import (
    _create,  # noqa: F401
    _delete,  # noqa: F401
    _detail,  # noqa: F401
    _export,  # noqa: F401
    _files,  # noqa: F401
    _list,  # noqa: F401
    _reprocess,  # noqa: F401
    _search,  # noqa: F401
    _speakers,  # noqa: F401
    _summary,  # noqa: F401
    _timestamps,  # noqa: F401
    _transcript,  # noqa: F401
    _update,  # noqa: F401
    _upload,  # noqa: F401
)
from ._common import router

__all__ = ["router"]
