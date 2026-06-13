"""Entry point so ``python3 -m services.discover`` works.

Delegates to :func:`services.discover.cli.main`; keeping the argparse logic
in ``cli.py`` lets tests import and call it directly without spawning a
subprocess.
"""

from services.discover.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
