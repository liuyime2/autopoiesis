"""Historical proxy simulation (research only).

Step 3 of docs/EXECUTION_PLAN_2026-11-05.md. Production never imports this.
"""

from .account import SimulatedAccount
from .clock import SimulationClock
from .runner import ReplayResult, run_replay

__all__ = ["ReplayResult", "SimulatedAccount", "SimulationClock", "run_replay"]
