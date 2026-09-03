# Flight Watcher v2

Alerts:
- ALL aircraft near home (default ceiling 15,000 ft)
- Military aircraft projected near LaVell Edwards Stadium
- N130TP active near PVU and projected toward home

Recommended deployment: Git-backed Portainer stack.
Required Portainer env vars: DISCORD_WEBHOOK_URL, HOME_LAT, HOME_LON.
Leave SEND_STARTUP_TEST=true for first deploy, then set false and redeploy.
Do not commit your real .env file.
