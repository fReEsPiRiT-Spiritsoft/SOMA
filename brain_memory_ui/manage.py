#!/usr/bin/env python
"""Django management command for SOMA-AI Brain Memory UI."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core_settings.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django not installed. Install via: pip install django"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
