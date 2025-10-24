"""
Introduce military AC. These are normal AC, which inherit the standard ADS-B plugins.

Use self.role to identify roles. I think in this case self is better than traf, since we won't need to export these
data elsewhere I think. roles can be CIVIL or MILITARY. Eventually, MILITARY could be like CRUISE, PATROL, FORMATION, INTERCEPT, LOW_LEVEL, RETURN_TO_BASE, etc...

At the moment, BlueSky/OpenAP do not have military aircraft performance, and in particular there are no jets.
The closest ones (sometimes used for certain non-fighting operations) could be B737 as AWACS platform or GLF6 for some command/VIP platform.
This is important, because if we select an AC type that is not supported by openAP, it defaults to B744.

For the interaction, we could model military AC in such a way that they are not affected by Conflict Detection/Resolution:
they never deviate from their path, but other AC are forced to move away from them.
"""
