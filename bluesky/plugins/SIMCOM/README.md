# SIMCOM

SIMCOM is a BlueSky plugin that extends the core simulator with ADS-B-specific functionality, enabling users to model aircraft communications, receivers, and attacker behavior in a realistic air traffic management (ATM) environment. The plugin allows simulation and analysis of cyber-attacks on ADS-B, including message replay, jamming, spoofing, ghost injection, and the evaluation of security measures such as AES-GCM encryption.

It is designed for researchers and engineers interested in studying the impact of cyber-attacks on air traffic operations, evaluating mitigation strategies, and testing security schemes while maintaining the high-level dynamics of ATM networks. SIMCOM integrates message propagation, noise, and receiver selection logic, allowing attacks and countermeasures to be evaluated in the context of realistic flight scenarios.

The architecture is modular and mirrors real-world ATM elements. The **core plugin** orchestrates all components and manages logging. The **World** module handles aircraft, attackers, and receivers, including propagation, noise, and distance-based constraints. Each **Aircraft** maintains its ADS-B Out registry, emits messages, and optionally encrypts them. **Attackers** can intercept, modify, or inject messages, while **Receivers** collect transmissions, decode them according to the security scheme, and provide data to the GUI or conflict detection module. Message propagation is computed with realistic time delays, but assumed to occur within a single simulation timestep, reflecting the small propagation time relative to ATM operations. This setup allows detailed analysis of both cyber-threats and defensive strategies while keeping simulations computationally efficient.

## List of Available Commands

### Surveillance

- `SURVEILLANCE acid, status[0/1/2]` – Set surveillance status for aircraft.

### Attacker Commands

- `ATTACK FREEZE acid` – Replay last known ADS-B message for selected aircraft.
- `ATTACK HIDE acid` – Simulate selective jamming of aircraft transmissions.
- `ATTACK JUMP acid, lat-diff, lon-diff, alt-diff` – Inject sudden position changes.
- `ATTACK GHOST acid, lat, lon, hdg, alt, spd` – Inject ghost aircraft with specified state.
- `ATTACK NONE acid` – Remove attack from aircraft.
- `ATTACK STATUS acid` – Show current attack status for aircraft.
- `ATTACK RESET` – Reset all active attacks.
- `ATTACK TOGGLE [flag]` – Enable or disable attacker logic globally.
- `ATTACK GHOSTCONF acid, targetacid, dpsi, cpa, tlosh` – Create ghost aircraft in conflict with target aircraft.
- `DELGHOST [acid]` – Delete a ghost aircraft.
- `MGHOST num` – Create multiple randomly-generated ghost aircraft.

### Conflict Detection Settings

- `ADSBZONE radius, acid` – Define conflict detection radius for given aircraft.
- `ADSBCD [flag]` – Enable/disable ADS-B conflict deteciton module.
- `ADSBDTLOOK [time],[acid]` – Set lookahead time for a given aircraft.
- `SHOWDANGER [flag]` – Highlight aircraft with anomalous surveillance status.
- `SHOWADSB [flag]` – Toggle ADS-B visualization.
- `SHOWADSBPZ [flag]` – Show ADS-B protected zones.

### Views

- `TOGGLEVIEW [flag (1/2/3)]` – Toggle between ADS-B traffic view, ground-truth view or both.
- `RXVIEW 0/1/2/...` – Select which receiver is visualized. 0 represents the global view.
- `ATKRANGE 0/1` – Enable or disable attacker range checking.

### Security

- `SECURITY AES-GCM [acid]` – Enable AES-GCM encryption for aircraft.
- `SECURITY NONE [acid]` – Disable security for aircraft.
- `SECURITY STATUS acid` – Display security status of aircraft.
- `SECURITY TOGGLE [flag]` – Enable or disable security module globally.

### Military Aircraft

- `MILCRE acid, lat, lon, hdg, alt, spd` – Create a simple military aircraft.
- `ROLE acid, [role (CIVIL/MILITARY)]` – Set the role of an aircraft.

---

SIMCOM is optimized for mid-level ATM simulations where propagation times are small compared to the simulation timestep. It models attacks, security, and ADS-B message flows without requiring full RF-level fidelity, providing a balance between realism and computational efficiency.
