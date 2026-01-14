"""
This module should implement network-level dynamics in SIMCOM.

It should be agnostic of whether this applies to attacks or receivers:
it should just implement attributes and functions to include and compute
areas of effect and distances to points.

This class is then imported by receivers.py and attacks.py to create distance-based effects.
"""
