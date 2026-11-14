import sys
from pathlib import Path

from integration.sources.conftest import root as root
from integration.sources.conftest import source_caller as source_caller
from integration.sources.conftest import source_services as source_services

# Maintained repository tools are source, not a second application distribution.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
