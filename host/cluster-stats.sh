#!/bin/bash
# Writes ${OBELISK_APPDATA:-/mnt/user/appdata/ark}/Shared/cluster_stats.json for the status page.
# Deployed by Obelisk and run every 2 minutes by a host scheduler.
# every 2 minutes by the User Scripts job "ark-cluster-stats" (Custom: */2 * * * *).
python3 - <<'PYEOF'
import json, os, subprocess, time, datetime
OUT="${OBELISK_APPDATA:-/mnt/user/appdata/ark}/Shared/cluster_stats.json"
def sh(a):
    try: return subprocess.run(a,capture_output=True,text=True,timeout=60).stdout
    except Exception: return ""
env=sh(["docker","inspect","-f","{{range .Config.Env}}{{println .}}{{end}}","obelisk"])
servers=""
for line in env.splitlines():
    if line.startswith("SERVERS="): servers=line[8:]
pairs=[]
for part in servers.split(","):
    if "=" in part:
        lab,hp=part.split("=",1)
        pairs.append((lab.strip(),hp.split(":")[0].strip()))
if not pairs: raise SystemExit("no servers found in obelisk env")
names=[c for _,c in pairs]
st=sh(["docker","stats","--no-stream","--format","{{.Name}}|{{.MemUsage}}|{{.MemPerc}}"]+names)
usage={}
for line in st.splitlines():
    f=line.split("|")
    if len(f)>=3: usage[f[0].strip()]={"mem":f[1].split("/")[0].strip(),"pct":f[2].strip()}
out={}
now=datetime.datetime.now(datetime.timezone.utc)
for lab,cn in pairs:
    d={}
    u=usage.get(cn)
    if u:
        d["mem"]=u["mem"]
        try: d["mem_pct"]=round(float(u["pct"].rstrip("%")),1)
        except Exception: d["mem_pct"]=None
    ins=sh(["docker","inspect","-f","{{.RestartCount}}|{{.State.StartedAt}}|{{.State.Status}}",cn]).strip()
    if ins:
        p=(ins.split("|")+["","",""])[:3]
        try: d["restarts"]=int(p[0])
        except Exception: pass
        d["status"]=p[2]
        try:
            ts=p[1].split(".")[0].rstrip("Z")
            dt=datetime.datetime.strptime(ts,"%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            s=int((now-dt).total_seconds())
            d["uptime"]=("%dd %dh"%(s//86400,(s%86400)//3600)) if s>=86400 else ("%dh %dm"%(s//3600,(s%3600)//60))
        except Exception: pass
    out[lab]=d
out["_ts"]=int(time.time())
os.makedirs(os.path.dirname(OUT),exist_ok=True)
tmp=OUT+".tmp"
fh=open(tmp,"w"); json.dump(out,fh); fh.close()
os.replace(tmp,OUT)
os.chmod(OUT,0o644)
print("wrote",OUT,"maps=",len(out)-1)
PYEOF
