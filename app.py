import json, math, os, time
from datetime import datetime, timezone
from pathlib import Path
import requests

def f(name, default): return float(os.getenv(name, str(default)))
BASE=os.getenv('ADSBLOL_BASE_URL','https://api.adsb.lol').rstrip('/')
POLL=f('POLL_SECONDS',10); SEARCH_RADIUS=f('SEARCH_RADIUS_NM',50)
WEBHOOK=os.environ['DISCORD_WEBHOOK_URL']
HOME_LAT=float(os.environ['HOME_LAT']); HOME_LON=float(os.environ['HOME_LON'])
HOME_MAX=f('HOME_MAX_ALTITUDE_FT',15000); HOME_EARLY_R=f('HOME_EARLY_RADIUS_NM',3); HOME_EARLY_ETA=f('HOME_EARLY_ETA_MIN',15)
HOME_IMM_R=f('HOME_IMMEDIATE_RADIUS_NM',1.5); HOME_IMM_ETA=f('HOME_IMMEDIATE_ETA_MIN',5)
STAD_LAT=f('STADIUM_LAT',40.25753); STAD_LON=f('STADIUM_LON',-111.65456); STAD_MAX=f('STADIUM_MAX_ALTITUDE_FT',15000)
STAD_EARLY_R=f('STADIUM_EARLY_RADIUS_NM',5); STAD_EARLY_ETA=f('STADIUM_EARLY_ETA_MIN',15)
STAD_IMM_R=f('STADIUM_IMMEDIATE_RADIUS_NM',2); STAD_IMM_ETA=f('STADIUM_IMMEDIATE_ETA_MIN',5)
TAIL=os.getenv('WATCHED_TAIL','N130TP').strip().upper()
PVU_LAT=f('PVU_LAT',40.2192); PVU_LON=f('PVU_LON',-111.7234); PVU_ELEV=f('PVU_ELEV_FT',4497); PVU_R=f('PVU_START_RADIUS_NM',2); PVU_LOW=f('PVU_LOW_ALT_AGL_FT',800)
EARLY_CD=f('EARLY_COOLDOWN_MINUTES',45)*60; IMM_CD=f('IMMEDIATE_COOLDOWN_MINUTES',30)*60; PVU_CD=f('PVU_COOLDOWN_MINUTES',30)*60
STARTUP=os.getenv('SEND_STARTUP_TEST','true').lower() in ('1','true','yes','y')
STATE_FILE=Path('/data/state.json'); s=requests.Session(); s.headers.update({'User-Agent':'home-flight-watcher/2.0'})
try: state=json.loads(STATE_FILE.read_text())
except Exception: state={}

def save(): STATE_FILE.write_text(json.dumps(state,indent=2))
def dist(a,b,c,d):
    r=3440.065; p1=math.radians(a); p2=math.radians(c); dp=math.radians(c-a); dl=math.radians(d-b)
    x=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(x))
def xy(lat,lon,rlat,rlon): return ((lon-rlon)*60*math.cos(math.radians(rlat)),(lat-rlat)*60)
def cpa(ac,tlat,tlon,maxmin):
    lat,lon,gs,tr=ac.get('lat'),ac.get('lon'),ac.get('gs'),ac.get('track')
    if not all(isinstance(v,(int,float)) for v in (lat,lon,gs,tr)) or gs<15: return None
    x,y=xy(lat,lon,tlat,tlon); sp=gs/60; th=math.radians(tr); vx=sp*math.sin(th); vy=sp*math.cos(th); vv=vx*vx+vy*vy
    if vv<=0:return None
    t=-(x*vx+y*vy)/vv
    if t<0 or t>maxmin:return None
    return t, math.hypot(x+vx*t,y+vy*t)
def alt(ac):
    a=ac.get('alt_baro')
    if a=='ground': return 0
    if isinstance(a,(int,float)): return float(a)
    a=ac.get('alt_geom'); return float(a) if isinstance(a,(int,float)) else None
def ident(ac): return str(ac.get('r') or '').strip().upper(), str(ac.get('flight') or '').strip().upper()
def key(ac):
    r,c=ident(ac); return str(ac.get('hex') or r or c or 'unknown').lower()
def military(ac):
    try:return bool(int(ac.get('dbFlags',0)) & 1)
    except:return False
def ok(k,cd): return time.time()-float(state.get(k,0))>=cd
def mark(k): state[k]=time.time(); save()
def send(title,desc,ac,emoji='✈️'):
    r,c=ident(ac); a=alt(ac)
    fields=[('Callsign',c or 'unknown'),('Registration',r or 'unknown'),('Type',str(ac.get('t') or ac.get('desc') or 'unknown')),('Altitude','ground' if a==0 else (f'{a:,.0f} ft' if a is not None else 'unknown')),('Groundspeed',f"{ac.get('gs','?')} kt"),('Track',f"{ac.get('track','?')}°"),('Source',str(ac.get('type') or 'unknown')),('ICAO Hex',str(ac.get('hex') or 'unknown'))]
    payload={'username':'Flight Watcher','embeds':[{'title':f'{emoji} {title}','description':desc,'fields':[{'name':n,'value':v,'inline':True} for n,v in fields],'timestamp':datetime.now(timezone.utc).isoformat()}]}
    rr=s.post(WEBHOOK,json=payload,timeout=10); rr.raise_for_status()
def fetch():
    rr=s.get(f'{BASE}/v2/point/{HOME_LAT}/{HOME_LON}/{SEARCH_RADIUS}',timeout=15); rr.raise_for_status(); return rr.json().get('ac',[])
def eval_home(ac):
    lat,lon=ac.get('lat'),ac.get('lon')
    if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)): return
    a=alt(ac)
    if a is not None and a>HOME_MAX:return
    k=key(ac); cur=dist(lat,lon,HOME_LAT,HOME_LON); p=cpa(ac,HOME_LAT,HOME_LON,HOME_EARLY_ETA)
    immediate=cur<=HOME_IMM_R; eta=None; miss=cur
    if p and p[0]<=HOME_IMM_ETA and p[1]<=HOME_IMM_R: immediate=True; eta,miss=p
    if immediate:
        sk=f'home_immediate:{k}'
        if ok(sk,IMM_CD): send('GO OUTSIDE — aircraft near home',f'Current distance **{cur:.1f} NM**. Closest/current pass **{miss:.1f} NM**; ETA **{"now" if eta is None else f"~{eta:.0f} min"}**.',ac,'🚨'); mark(sk)
        return
    if p and p[1]<=HOME_EARLY_R:
        eta,miss=p; sk=f'home_early:{k}'
        if ok(sk,EARLY_CD): send('Aircraft heading toward home',f'Current distance **{cur:.1f} NM**. Projected closest approach **{miss:.1f} NM** in **~{eta:.0f} min**.',ac); mark(sk)
def eval_stadium(ac):
    if not military(ac): return
    lat,lon=ac.get('lat'),ac.get('lon')
    if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)): return
    a=alt(ac)
    if a is not None and a>STAD_MAX:return
    k=key(ac); cur=dist(lat,lon,STAD_LAT,STAD_LON); p=cpa(ac,STAD_LAT,STAD_LON,STAD_EARLY_ETA)
    immediate=cur<=STAD_IMM_R; eta=None; miss=cur
    if p and p[0]<=STAD_IMM_ETA and p[1]<=STAD_IMM_R: immediate=True; eta,miss=p
    if immediate:
        sk=f'stadium_immediate:{k}'
        if ok(sk,IMM_CD): send('MILITARY FLYOVER IMMINENT — LaVell Edwards Stadium',f'Current stadium distance **{cur:.1f} NM**. Closest/current pass **{miss:.1f} NM**; ETA **{"now" if eta is None else f"~{eta:.0f} min"}**.',ac,'🇺🇸'); mark(sk)
        return
    if p and p[1]<=STAD_EARLY_R:
        eta,miss=p; sk=f'stadium_early:{k}'
        if ok(sk,EARLY_CD): send('Military aircraft heading toward LaVell Edwards Stadium',f'Current stadium distance **{cur:.1f} NM**. Projected closest approach **{miss:.1f} NM** in **~{eta:.0f} min**.',ac,'🇺🇸'); mark(sk)
def eval_tail(ac):
    r,c=ident(ac)
    if TAIL not in (r,c):return
    lat,lon=ac.get('lat'),ac.get('lon')
    if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)):return
    pd=dist(lat,lon,PVU_LAT,PVU_LON); a=alt(ac)
    if pd<=PVU_R and (a==0 or (a is not None and a<=PVU_ELEV+PVU_LOW)):
        sk=f'pvu:{TAIL}'
        if ok(sk,PVU_CD):send(f'{TAIL} active at PVU',f'Detected **{pd:.1f} NM** from PVU at low altitude/on ground.',ac,'🚁');mark(sk)
    cur=dist(lat,lon,HOME_LAT,HOME_LON); p=cpa(ac,HOME_LAT,HOME_LON,HOME_EARLY_ETA)
    if p and p[1]<=HOME_EARLY_R:
        eta,miss=p; sk=f'watched_home:{TAIL}'
        if ok(sk,EARLY_CD):send(f'{TAIL} heading toward home',f'Current distance **{cur:.1f} NM**. Projected closest approach **{miss:.1f} NM** in **~{eta:.0f} min**.',ac,'🚁');mark(sk)
def main():
    print(f'Flight Watcher v2: radius={SEARCH_RADIUS} NM tail={TAIL}')
    if STARTUP: send('Flight Watcher online',f'Watching all low-altitude traffic near home, military traffic near LaVell Edwards Stadium, and **{TAIL}**.',{},'✅')
    while True:
        start=time.time()
        try:
            acs=fetch(); print(f'Received {len(acs)} aircraft')
            for ac in acs:
                try: eval_home(ac); eval_stadium(ac); eval_tail(ac)
                except Exception as e: print('Aircraft error',key(ac),type(e).__name__,e)
        except Exception as e: print('Feed error',type(e).__name__,e)
        time.sleep(max(1,POLL-(time.time()-start)))
if __name__=='__main__': main()
