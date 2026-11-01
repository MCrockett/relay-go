#!/usr/bin/env python3.11
"""Is it safe to pull the live relay now? Lists what is in flight; exits 0 when nothing is.

  tools/deploy_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from relaylib import deploycheck  # noqa: E402

if __name__ == "__main__":
    sys.exit(deploycheck.main())
