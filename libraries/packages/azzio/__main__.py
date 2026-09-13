"""Allow `python -m packages.azzio ...` to run the command line interface in-place (dev/testing).

The SHIPPED artifact is always the single bundled script (bundle.bundle_source(), installed
to /usr/local/bin/azzio). This entry point just wires the split package's command_line_interface.main() so the
command line interface is runnable from the source tree without bundling first.
"""

from __future__ import annotations

import sys

# The split source modules assume ONE namespace (they call helpers by bare name, matching
# how they are bundled). To honour that when running from the package, execute the bundle
# text in a single namespace rather than importing each module separately.
from .bundle import bundle_source

if __name__ == "__main__":
    ns: dict = {"__name__": "__azzio_main__"}
    exec(compile(bundle_source(), "azzio-bundle", "exec"), ns)
    sys.exit(ns["main"]())
