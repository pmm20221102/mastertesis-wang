#!/usr/bin/env python3
"""Convert Khronos PLY/DSG geometry to a trinary Nav2 occupancy map."""
import argparse, hashlib, json, math, pathlib, tempfile

RES=.10
def sha(p):
 h=hashlib.sha256();
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
def ply_points(path):
 with open(path,encoding='ascii') as f:
  line=f.readline(); assert line.strip()=='ply'
  n=0
  for line in f:
   if line.startswith('element vertex '): n=int(line.split()[2])
   if line.strip()=='end_header': break
  if not n: raise ValueError('empty PLY')
  for _ in range(n):
   a=f.readline().split();
   if len(a)<3: raise ValueError('malformed vertex')
   yield tuple(map(float,a[:3]))
def places(path):
 with open(path,encoding='utf-8') as f: d=json.load(f)
 nodes={}
 for n in d.get('nodes',[]):
  if n.get('layer') not in (3,'PLACES') and n.get('layer_name')!='PLACES': continue
  a=n.get('attributes',{}); p=a.get('position') or a.get('world_position')
  if not p and isinstance(a.get('bounding_box'),dict): p=a['bounding_box'].get('world_P_center')
  if isinstance(p,(list,tuple)) and len(p)>=3 and all(math.isfinite(float(x)) for x in p[:3]): nodes[n['id']]=tuple(map(float,p[:3]))
 if not nodes: raise ValueError('DSG has no PLACES positions')
 adj={k:set() for k in nodes}; graph_edges=[]
 for e in d.get('edges',[]):
  a,b=e.get('source'),e.get('target')
  if a in nodes and b in nodes and a!=b:
   adj[a].add(b); adj[b].add(a)
   weight=e.get('info',{}).get('weight')
   graph_edges.append((a,b,float(weight) if weight is not None else math.dist(nodes[a],nodes[b])))
 components=[]; seen=set()
 for start in nodes:
  if start in seen: continue
  stack=[start]; seen.add(start); component=[]
  while stack:
   u=stack.pop(); component.append(u)
   for v in adj[u]:
    if v not in seen: seen.add(v); stack.append(v)
  components.append(component)
 component=max(components,key=len)
 if len(component)<5: raise ValueError('largest DSG Place component has fewer than five nodes')
 selected=set(component); edges=[e for e in graph_edges if e[0] in selected and e[1] in selected]
 if not edges: raise ValueError('largest DSG Place component has no Place edges')
 zs=sorted(nodes[i][2] for i in component); z=zs[0]; band_top=z+0.25
 band_ids={i for i in component if z <= nodes[i][2] <= band_top}
 free=[nodes[i] for i in band_ids]
 if len(free)<5: raise ValueError('strict single-floor Place z-band has fewer than five nodes')
 # A second floor must have a comparably populated, separated low band. Higher
 # Higher samples can be vertical Place nodes rather than separate floor levels.
 low_levels=[]
 for level in sorted(set(round(nodes[i][2],2) for i in component)):
  count=sum(1 for i in component if abs(nodes[i][2]-level)<0.06)
  if count >= max(20,int(0.8*len(free))): low_levels.append(level)
 if len(low_levels)>1 and low_levels[1]-low_levels[0] > 1.0: raise ValueError('multiple populated Place floor bands detected')
 band_edges=[(nodes[a],nodes[b],weight) for a,b,weight in edges if a in band_ids and b in band_ids and weight <= 1.3]
 if not band_edges: raise ValueError('strict z-band has no short recorded Place edges')
 return free,z,{'place_nodes':len(nodes),'place_edges':len(graph_edges),'component_nodes':len(component),'component_edges':len(edges),'z_levels':len(set(round(nodes[i][2],2) for i in component)),'floor_band':[z,band_top],'multi_floor_checked':True},band_edges
def convert(mesh,dsg,out):
 pts=list(ply_points(mesh)); free,z,place_meta,place_edges=places(dsg)
 band=[p for p in pts if z+.18<=p[2]<=z+1.8]
 if not band: raise ValueError('no obstacle band')
 xs=[p[0] for p in band+free]; ys=[p[1] for p in band+free]; pad=1.0
 ox=math.floor((min(xs)-pad)/RES)*RES; oy=math.floor((min(ys)-pad)/RES)*RES
 w=int(math.ceil((max(xs)+pad-ox)/RES)); h=int(math.ceil((max(ys)+pad-oy)/RES)); grid=[205]*(w*h); support=set(); support_radius=.55
 def cell(x,y): return int((x-ox)/RES),int((y-oy)/RES)
 for x,y,_ in free:
  i,j=cell(x,y); radius=int(math.ceil(support_radius/RES))
  for di in range(-radius,radius+1):
   for dj in range(-radius,radius+1):
    if (di*RES)**2+(dj*RES)**2<=support_radius**2 and 0<=i+di<w and 0<=j+dj<h: support.add((i+di,j+dj))
 # Support follows real DSG Place edges, never synthetic geometric neighbors.
 # Rasterize each recorded edge as a capsule: a centerline alone leaves gaps
 # when adjacent Place spacing exceeds 2*support_radius.
 for (ax,ay,_),(bx,by,_),weight in place_edges:
   lo_i,hi_i=sorted((int((min(ax,bx)-support_radius)/RES),int((max(ax,bx)+support_radius)/RES)))
   lo_j,hi_j=sorted((int((min(ay,by)-support_radius)/RES),int((max(ay,by)+support_radius)/RES)))
   dx,dy=bx-ax,by-ay; denom=dx*dx+dy*dy
   for i in range(lo_i,hi_i+1):
    for j in range(lo_j,hi_j+1):
     if not (0<=i<w and 0<=j<h): continue
     px,py=ox+(i+.5)*RES,oy+(j+.5)*RES
     t=max(0.,min(1.,((px-ax)*dx+(py-ay)*dy)/denom)) if denom else 0.
     if (px-(ax+t*dx))**2+(py-(ay+t*dy))**2<=support_radius**2: support.add((i,j))
 for x,y,_ in band:
  i,j=cell(x,y)
  if 0<=i<w and 0<=j<h: grid[j*w+i]=0
 seeded=sum(1 for i,j in support if grid[j*w+i]!=0); covered=len(support)-seeded
 for i,j in support:
  # Real mesh obstacles have priority over Place support.
  if grid[j*w+i]!=0: grid[j*w+i]=254
 out=pathlib.Path(out); out.mkdir(parents=True,exist_ok=True)
 with tempfile.NamedTemporaryFile('w',dir=out,delete=False) as f:
  f.write('P2\n%d %d\n255\n'% (w,h));
  for j in range(h-1,-1,-1): f.write(' '.join(map(str,grid[j*w:(j+1)*w]))+'\n')
  pgm=pathlib.Path(f.name)
 pgm.rename(out/'map.pgm')
 (out/'map.yaml').write_text('image: map.pgm\nresolution: 0.1\norigin: [%g, %g, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n'%(ox,oy))
 report={'inputs':{str(x):sha(pathlib.Path(x)) for x in (mesh,dsg)},'frame':'map','height_contract':{'place_floor_source':'lowest populated Place band','nav2_z':0.0,'mesh_obstacle_band':'floor_z+0.18..floor_z+1.8'},'resolution':RES,'origin':[ox,oy,0.0],'width':w,'height':h,'floor_z':z,'z_band':[z+.18,z+1.8],'mesh_band_points':len(band),'place_count':len(free),'support_radius':support_radius,'support_cells':len(support),'seeded_free_cells':seeded,'seed_occupied_overlap':covered,'place_graph':place_meta,'cells':{'unknown':grid.count(205),'free':grid.count(254),'occupied':grid.count(0)}}
 assert report['seeded_free_cells'] > 0, 'all free-space seeds became occupied'
 (out/'report.json').write_text(json.dumps(report,indent=2)+'\n'); return report
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--mesh',required=True); p.add_argument('--dsg',required=True); p.add_argument('--output',required=True); a=p.parse_args(); print(json.dumps(convert(a.mesh,a.dsg,a.output),indent=2))
