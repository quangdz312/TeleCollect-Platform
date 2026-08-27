"""Compatibility entry point for TeleCollect Local.

This module is kept so older shortcuts importing ``local_app.desktop`` keep
working with the supported web-shell implementation.
"""

from local_app.electron_launcher import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
