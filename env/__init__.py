"""MaxQ Phase 2 — Mission Environment Package.

Simulates end-to-end missions through ASCENT -> PARKING -> PHASING
(and later APPROACH -> DOCK) starting from a contract-valid mission_request.
"""

from env.mission_env import SIM_VERSION, MissionEnv

__all__ = ["MissionEnv", "SIM_VERSION"]
