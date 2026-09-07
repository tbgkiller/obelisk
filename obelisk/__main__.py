"""`python -m obelisk ...` - the shell-side commands. The server is obelisk.app."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
