#!/usr/bin/env python3
import argparse, json, pathlib
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--corpus',required=True); ap.add_argument('--batch',required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); rows=[]; batch=json.loads(pathlib.Path(a.batch).read_text())
 for r in batch['rows']:
  d={}; p=pathlib.Path(r['output'])
  if p.is_file():
   try: d=json.loads(p.read_text())
   except json.JSONDecodeError: pass
  chain=d.get('task_chain') or d.get('result',{}).get('task_chain') or d.get('pipeline',{}).get('task_chain') or []
  rows.append({'case_id':r['case_id'],'semantic_pass':r['returncode']==0 and len(chain)>=5,'waypoint_count':len(chain),'output_sha256':r.get('output_sha256'),'error':r.get('error')})
 pathlib.Path(a.output).write_text(json.dumps({'schema_version':'semantic-eval-v1','rows':rows,'semantic_passes':sum(x['semantic_pass'] for x in rows)},indent=2)+'\n')
if __name__=='__main__': main()
