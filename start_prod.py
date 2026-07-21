#!/usr/bin/env python3
"""Production entry point — maps platform PORT to HARNESS_API_PORT."""
import os
import sys

# Render/Fly.io inject PORT, but LingShu uses HARNESS_API_PORT
if "PORT" in os.environ and "HARNESS_API_PORT" not in os.environ:
    os.environ["HARNESS_API_PORT"] = os.environ["PORT"]

# Ensure binding to all interfaces
if "HARNESS_API_HOST" not in os.environ:
    os.environ["HARNESS_API_HOST"] = "0.0.0.0"

# Run the real entry point
from agent_harness.main import main
main()
