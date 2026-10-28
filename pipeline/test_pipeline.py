"""Software tests; no ARC model requests or research observations."""
import csv, json, os, subprocess, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from pathlib import Path
from arc_study import Client, score, scalar, messages, validate, build_jobs, NATURAL_STRATEGIES

ROOT=Path(__file__).resolve().parent

class Tests(unittest.TestCase):
 def test_http_transport_and_logged_retry(self):
  seen=[]
  class Handler(BaseHTTPRequestHandler):
   def do_POST(self):
    seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
    if len(seen)==1:
     self.send_response(429);self.end_headers();self.wfile.write(b'{"error":{"code":"test_rate_limit"}}');return
    self.send_response(200);self.end_headers()
    self.wfile.write(json.dumps({'model':'fixture','choices':[{'message':{'content':'FINAL: 2'},'finish_reason':'stop'}],'usage':{'prompt_tokens':7,'completion_tokens':3}}).encode())
   def log_message(self,*args):pass
  server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'FIXTURE_KEY':'TEST_ONLY_NOT_A_SECRET'}), patch('arc_study.time.sleep'):
    p=Path(tmp)/'calls.jsonl';m={'label':'fixture','model':'fixture','base_url':f'http://127.0.0.1:{server.server_port}/v1','key_env':'FIXTURE_KEY'}
    result=Client(m,p,'fixture-context').call([{'role':'user','content':'1+1?'}],{'phase':'preflight'}, {'reference_answer':'2'})
    records=[json.loads(l) for l in p.read_text().splitlines()]
    self.assertEqual([r['status'] for r in records],['error','success']);self.assertTrue(records[1]['is_technical_retry'])
    self.assertEqual(seen[0],seen[1]);self.assertNotIn('TEST_ONLY_NOT_A_SECRET',p.read_text())
    self.assertIsNone(result['reasoning_tokens']);self.assertIsNone(result['visible_tokens']);self.assertTrue(result['score']['correct'])
  finally:server.shutdown();server.server_close();thread.join()
 def test_numeric_parsing(self):
  c={'reference_answer':'11/36'}
  for text in ['FINAL: 11/36','**FINAL: 11 / 36**','FINAL: \\frac{11}{36}','FINAL: 0.3056']:
   self.assertTrue(score(text,c)['correct'],text)
  self.assertTrue(score('FINAL: 30%',{'reference_answer':'0.3'})['correct'])
  self.assertTrue(score('FINAL: 60%',{'reference_answer':'60','answer_unit':'percentage_points'})['correct'])
  for text in ['FINAL: 0.305555...','FINAL: 4/13 = 0.308','answer is 0.3','FINAL: __import__(x)']:
   self.assertIsNone(score(text,c)['correct'])
  self.assertFalse(score('**FINAL: 11/36**',c)['strict_format'])
 def test_seeded_cases_and_roles(self):
  cases=json.loads((ROOT/'cases_candidates.json').read_text(encoding='utf-8'));validate(cases)
  self.assertEqual(len(cases),113)
  ready=[c for c in cases if c['seed_ready']];self.assertEqual(len(ready),85)
  self.assertEqual(sum(c['source']=='AIME 2025' for c in cases),28)
  self.assertTrue(all(not c['seed_ready'] for c in cases if c['source']=='AIME 2025'))
  for c in ready:
   msg=messages(c,'wrong','targeted_fix');self.assertEqual([m['role'] for m in msg],['system','user','assistant','user'])
   self.assertEqual(msg[2]['content'],c['wrong_prior'])
  with self.assertRaises(ValueError):validate(cases,True)
 def test_end_to_end_fixture(self):
  with tempfile.TemporaryDirectory() as tmp:
   def call(*args):return subprocess.run([sys.executable,str(ROOT/'arc_study.py'),*args],cwd=ROOT,capture_output=True,text=True)
   out=Path(tmp)/'run';p=call('run','--phase','smoke','--simulate','--out',str(out));self.assertEqual(p.returncode,0,p.stderr)
   log=out/'SIMULATED_calls.jsonl';rows=[json.loads(l) for l in log.read_text().splitlines()]
   self.assertEqual(len(rows),180);self.assertTrue(all(r['is_simulated'] for r in rows))
   self.assertEqual(sum(r['round']==2 for r in rows),90)
   r2=next(r for r in rows if r['round']==2);self.assertEqual(len(r2['request']['messages']),6)
   self.assertIn('reasoning_content',r2['reasoning_fields']);self.assertEqual(r2['visible_tokens'],20)
   p=call('analyze','--log',str(log),'--out',str(Path(tmp)/'bad.json'));self.assertNotEqual(p.returncode,0)
   p=call('select','--log',str(log),'--out',str(Path(tmp)/'selected.json'));self.assertNotEqual(p.returncode,0)
   dest=Path(tmp)/'analysis.json';p=call('analyze','--log',str(log),'--allow-simulated','--out',str(dest));self.assertEqual(p.returncode,0,p.stderr)
   analysis=json.loads(dest.read_text());self.assertTrue(analysis['is_simulated'])
   gain=next(r for r in analysis['rounds'] if r['strategy']=='neutral_retry' and r['round']==2)
   self.assertEqual(gain['additional_corrected_chains_since_previous_round'],3)
   self.assertEqual(gain['tokens_per_additional_corrected_chain'],140)
   audit=Path(tmp)/'audit.csv';p=call('audit','--log',str(log),'--out',str(audit));self.assertEqual(p.returncode,0,p.stderr)
   with audit.open() as f:self.assertEqual(len(list(csv.DictReader(f))),50)

 def test_simpson_generator_reproducible_and_paradoxical(self):
  from fractions import Fraction as F
  import generate_simpson
  pool=[c for c in json.loads((ROOT/'cases_candidates.json').read_text(encoding='utf-8')) if c['source']=='Simpson generator']
  self.assertEqual(generate_simpson.build(),pool)
  for c in pool:
   rows=c['generator']['table']
   self.assertTrue(all(F(r['sA'],r['nA'])>F(r['sB'],r['nB']) for r in rows))
   self.assertLess(F(sum(r['sA'] for r in rows),sum(r['nA'] for r in rows)),F(sum(r['sB'] for r in rows),sum(r['nB'] for r in rows)))
 def test_natural_priors_jobs_and_messages(self):
  c={'case_id':'X','question':'q','reference_answer':'5','task_type':'t','tier':'h','seed_ready':False,
     'eligible_models':['m1'],'natural_priors':{'m1':{'wrong':'work\nFINAL: 4','correct':'work\nFINAL: 5'}}}
  m1={'label':'m1'};m2={'label':'m2'}
  jobs=build_jobs('study',[c],[m1,m2])
  self.assertTrue(all(j[1] is m1 for j in jobs))
  self.assertEqual(len(jobs),2*len(NATURAL_STRATEGIES)*3)
  self.assertNotIn('targeted_fix',{j[3] for j in jobs})
  msg=messages(c,'natural_wrong','self_check','m1');self.assertEqual(msg[2]['content'],'work\nFINAL: 4')
  validate([c])
  bad=dict(c,natural_priors={'m1':{'wrong':'FINAL: 5'}})
  with self.assertRaises(ValueError):validate([bad])
 def test_select_builds_natural_priors_per_model(self):
  import arc_study
  case={'case_id':'X','question':'q','reference_answer':'5','task_type':'t','tier':'h','source':'S','seed_ready':False}
  with tempfile.TemporaryDirectory() as tmp:
   tmp=Path(tmp);(tmp/'cases.json').write_text(json.dumps([case]));(tmp/'cfg.json').write_text(json.dumps({'models':[{'label':'m1'},{'label':'m2'}]}))
   rows=[]
   for label,answers in [('m1',['5','4','5','4','4']),('m2',['5']*5)]:
    for r,a in enumerate(answers,1):
     rows.append({'status':'success','phase':'calibrate','case_id':'X','model_label':label,'repeat':r,'logical_call_id':label+str(r),
                  'response':'FINAL: '+a,'score':score('FINAL: '+a,case)})
   (tmp/'calls.jsonl').write_text('\n'.join(json.dumps(x) for x in rows))
   ns=type('A',(),{'log':tmp/'calls.jsonl','cases':tmp/'cases.json','config':tmp/'cfg.json','out':tmp/'sel.json','band':[.3,.7],'max_per_model':50})
   arc_study.calibrate_select(ns)
   sel=json.loads((tmp/'sel.json').read_text())
   self.assertEqual(sel[0]['eligible_models'],['m1'])
   self.assertEqual(sel[0]['natural_priors']['m1']['wrong'],'FINAL: 4');self.assertEqual(sel[0]['natural_priors']['m1']['correct'],'FINAL: 5')
   self.assertNotIn('m2',sel[0]['natural_priors'])
 def test_resume_after_interruption(self):
  with tempfile.TemporaryDirectory() as tmp:
   def call(*args):return subprocess.run([sys.executable,str(ROOT/'arc_study.py'),*args],cwd=ROOT,capture_output=True,text=True)
   out=Path(tmp)/'run';p=call('run','--phase','smoke','--simulate','--out',str(out));self.assertEqual(p.returncode,0,p.stderr)
   log=out/'SIMULATED_calls.jsonl';lines=log.read_text(encoding='utf-8').splitlines()
   log.write_text('\n'.join(lines[:101])+'\n'+lines[101][:40],encoding='utf-8')   # crash mid-write
   p=call('run','--phase','smoke','--simulate','--out',str(out));self.assertEqual(p.returncode,0,p.stderr)
   self.assertIn('Resuming',p.stdout)
   rows=[json.loads(l) for l in log.read_text(encoding='utf-8').splitlines() if l.startswith('{') and l.endswith('}')]
   self.assertEqual(len(rows),180);self.assertEqual(len({r['logical_call_id'] for r in rows}),180)
   p=call('run','--phase','calibrate','--simulate','--out',str(out));self.assertNotEqual(p.returncode,0)

 def test_simpson_tolerance_only_for_simpson(self):
  s={'reference_answer':'-160763/10000','reference_decimal_places':2,'answer_unit':'percentage_points','task_type':'simpsons_paradox'}
  self.assertTrue(score('FINAL: -16.07',s)['correct'])      # rounded intermediates: within 0.05
  self.assertTrue(score('FINAL: -16.12',s)['correct'])
  self.assertFalse(score('FINAL: -16.20',s)['correct'])     # beyond tolerance
  self.assertFalse(score('FINAL: 16.08',s)['correct'])      # sign error
  self.assertFalse(score('FINAL: -16.07',dict(s,task_type='other'))['correct'])   # others keep strict rounding
  self.assertFalse(score('FINAL: 0.3056',{'reference_answer':'11/36','task_type':'Algebra'})['correct'] is False)

 def test_append_waits_out_a_brief_file_lock(self):
  import arc_study
  real_open=Path.open;calls=[0]
  def flaky(self,*a,**k):
   calls[0]+=1
   if calls[0]<=3:raise PermissionError(13,'Permission denied (simulated OneDrive lock)')
   return real_open(self,*a,**k)
  with tempfile.TemporaryDirectory() as tmp, patch('arc_study.time.sleep'), patch.object(Path,'open',flaky):
   arc_study.append(Path(tmp)/'x.jsonl',{'a':1})
  self.assertEqual(calls[0],4)

if __name__=='__main__':unittest.main(verbosity=2)
