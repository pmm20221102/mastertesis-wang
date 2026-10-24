#!/usr/bin/env python3
import argparse, hashlib, json, math, pathlib, re
FOOTPRINT=[(0.38,0.28),(0.38,-0.28),(-0.38,-0.28),(-0.38,0.28)]
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--map',required=True); ap.add_argument('--waypoints',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
 y=pathlib.Path(a.map).read_text(); origin=[float(x) for x in re.search(r'origin:\s*\[([^]]+)',y).group(1).split(',')]; res=float(re.search(r'resolution:\s*([0-9.]+)',y).group(1)); pgm=pathlib.Path(a.map).parent/re.search(r'image:\s*(\S+)',y).group(1); z=pgm.read_text().split(); w,h=int(z[1]),int(z[2]); vals=list(map(int,z[3:])); safe=lambda x,y: 0<=x<w and 0<=y<h and vals[(h-1-y)*w+x]>=250
 src=json.loads(pathlib.Path(a.waypoints).read_text())['waypoints']; rows=[]
 for i,p in enumerate(src):
  cx=round((p['x']-origin[0])/res-.5); cy=round((p['y']-origin[1])/res-.5); cells=[(cx+round(dx/res),cy+round(dy/res)) for dx,dy in FOOTPRINT]; ok=all(safe(x,y) for x,y in cells); rows.append({'index':i,'source_id':p.get('source_id'),'source_pose':p.get('source_pose',p),'nav2_pose':{'x':p['x'],'y':p['y'],'yaw':p.get('yaw',0.0),'frame':'map'},'footprint_safe':ok,'unknown_is_unsafe':True,'failure_category':None if ok else 'footprint_or_unknown'})
 out={'schema_version':'projection-v1','map_sha256':hashlib.sha256(pgm.read_bytes()).hexdigest(),'frame':'map','resolution':res,'origin':origin,'footprint':FOOTPRINT,'rows':rows,'eligible':all(x['footprint_safe'] for x in rows)}; pathlib.Path(a.output).write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
