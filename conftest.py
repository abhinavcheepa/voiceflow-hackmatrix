"""Pytest configuration for VoiceFlow AI test suite."""

import os
import pytest

# Ensure tests run with offline mode by default so no test hangs on fastembed downloads
os.environ.setdefault("VF_OFFLINE", "1")

