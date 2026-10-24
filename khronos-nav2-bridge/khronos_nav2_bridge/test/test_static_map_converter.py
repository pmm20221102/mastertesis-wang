#!/usr/bin/env python3
import pathlib, tempfile, sys
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'scripts'))
from mesh_to_nav2_map import convert
with tempfile.TemporaryDirectory() as t:
 p=pathlib.Path(t); (p/'m.ply').write_text('ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0.5\n1 0 0\n0 1 0\n1 1 0\n'); (p/'d.json').write_text('{"nodes":[' + ','.join('{"id":%d,"layer":3,"attributes":{"position":[%s,%s,0]}}' % (i,x,y) for i,(x,y) in enumerate(((0.2,0.2),(0.4,0.2),(0.6,0.2),(0.8,0.2),(0.8,0.4)))) + '],"edges":[' + ','.join('{"source":%d,"target":%d}' % (i,i+1) for i in range(4)) + ']}')
 r=convert(p/'m.ply',p/'d.json',p/'out'); assert r['width']>0 and (p/'out/map.pgm').is_file()
print('static converter: PASS')
