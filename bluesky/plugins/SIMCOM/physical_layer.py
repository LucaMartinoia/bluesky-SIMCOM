"""
This module should implement noise/jitter/lag effect to the ADS-B messages.

First, we should refactor the original update function in adsb_protocol to use Timers instead of a fixed rate at 0.5s.
Secondly, we can implement in this module delays/packet losses per aircraft. These are of two kind: delays are computed
at the source, these are just random statistical fluctuations that affect when the Timer actually fires a new ADS-B
message. Packet losses instead are due to noise along the path. A simple approach is to apply packet losses right AFTER
the ADS-B messages are computed. This way, we can simply set to "None" messages which are lost.
We could also think about specific bit-flip errors, but probably too advanced for now. Finally, we could also compute the overalps time:
if two messages arrive at a given receiver with a delay of less than 150 micro second at least one of them is dropped.

Finally, we could have a stack function or a setting that defines a position (lat/lon) that represent the physical
position of the receiver. Then, the packet loss rate can be scaled with respect to this position (30%+ and growing),
potentially also including curvature effect and loss of line of sight, so that aircraft which are too far from the
point are really invisible.

The packet losses should be computed PER aircraft and PER receiver. The receiver, for now, are just ground ones, but in
the future we could consider also aircraft receivers. The GUI then "hooks" on a SPECIFIC ground receiver (or acts as a all-knowing entity)
and displays only the messages received by a single receiver.

This way we are simulating a very basic physical+network layer, but without going down to the sub micro-second EM physics and signal processing.

This could also be two module: one that computes the noise and so on (like adsb_encoder computes the ADS-B messages) and another one that
uses these functions and implement the network-level aspects.
"""
