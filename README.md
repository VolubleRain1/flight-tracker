# Flight Watcher v3

Drop-in replacement for v2.

Main change: predictive home and stadium alerts now require:
- heading toward the target within a configurable angular tolerance
- multiple consecutive samples showing decreasing distance
- projected closest approach inside the configured radius
- ETA inside the configured warning window

Required Portainer variables:
- DISCORD_WEBHOOK_URL
- HOME_LAT
- HOME_LON

Recommended defaults are already in docker-compose.yml.
