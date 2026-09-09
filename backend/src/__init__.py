"""Meeting Agent backend package bootstrap."""

import os

# SQLite, Chroma, parsers, and trace writers can create files while modules are
# imported. Set the private process umask at package entry, before submodules.
os.umask(0o077)
