#!/usr/bin/env python3
"""ARC correction study: standard-library HTTP, explicit logs, no credentials in files.
Simulation is a software check only. It is never research evidence.

v2.0 changes (see deviations.csv): natural-error arm built from calibration answers, per-model
calibration eligibility, automatic resume of interrupted runs, parallel workers, per-model
max_tokens, AIME 2025 + generated Simpson's paradox candidates.
"""
import argparse, ast, copy, csv, hashlib, itertools, json, math, os, threading
import platform, random, re, sys, time, urllib.error, urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, FIRST_EXCEPTION, wait
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

VERSION = 'part1-2.0'
SYSTEM = 'You are a mathematical problem solver. Evaluate the supplied solution accurately.'
FINAL = 'Give a concise explanation and end with exactly one line: FINAL: <numeric answer>. Use a decimal, fraction, or percentage; do not add units or approximation text to that line.'
STRATEGIES = {
 'neutral_retry': 'Revisit the original question and give your own final answer.',
 'error_flagging': 'The previous answer may be incorrect. Check it carefully and correct it if needed.',
 'targeted_fix': 'Check this possible source of error: {hint}. This is a check, not a claim that an error occurred. Correct the solution only if needed.',
 'step_by_step_redo': 'Solve the question again from the beginning, showing the key calculation steps. Then provide the final answer.',
 'self_check': 'Solve independently, then verify the result using a separate check before giving the final answer.'
}
SEEDED_PRIORS = ['wrong', 'correct']
NATURAL_PRIORS = ['natural_wrong', 'natural_correct']
# Natural errors have no known error location, so there is no honest targeted hint to give.
NATURAL_STRATEGIES = [s for s in STRATEGIES if s != 'targeted_fix']
# Absolute tolerance (in the answer's own units) by task type. Simpson cases are in percentage
# points: +/-0.05 accepts rounding intermediates before subtracting, while every seeded error is
# >=0.13 points away. Scores are recomputed from the saved response text in select/analyze, so a
# rule change never requires re-running models (declared in deviations.csv).
TOLERANCE_BY_TASK = {'simpsons_paradox': Fraction(1, 20)}
CALIBRATION_REPEATS = 5
WRONG_ROUNDS = 3
LOCK = threading.Lock()

def utc(): return datetime.now(timezone.utc).isoformat()
def digest(x): return hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()
def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def _retry_io(fn, tries=40):
 """OneDrive/Dropbox/antivirus can lock a file for a moment on Windows; wait instead of crashing."""
 for i in range(tries):
  try: return fn()
  except PermissionError:
   if i==tries-1: raise
   time.sleep(min(0.25*(i+1),3))
def save(p, x): _retry_io(lambda: Path(p).write_text(json.dumps(x, indent=2, ensure_ascii=False), encoding='utf-8'))
def append(p, x):
 line=json.dumps(x, ensure_ascii=False)+'\n'
 def write():
  with Path(p).open('a', encoding='utf-8') as f: f.write(line)
 with LOCK: _retry_io(write)
def read_log(p):
 rows=[]
 for line in _retry_io(lambda: Path(p).read_text(encoding='utf-8')).splitlines():
  try: rows.append(json.loads(line))
  except json.JSONDecodeError: pass   # a line cut off by a crash; the call is simply redone on resume
 return rows

def scalar(text, unit='number'):
 """Only numeric scalars: exact fractions/decimals/percentages. No eval or algebra guesses."""
 s=text.strip().replace('$','').replace('\\(','').replace('\\)','')
 s=s.replace('\\%','%').replace('\\,','').replace(' ','').replace(',','')
 s=re.sub(r'\\(?:d?frac)\{([+-]?\d+)\}\{([+-]?\d+)\}',r'\1/\2',s)
 s=s.strip('*')
 percent=s.endswith('%')
 if percent:s=s[:-1]
 if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:/[+-]?\d+(?:\.\d*)?)?',s):
  raise ValueError('Unsupported numeric answer')
 v=Fraction(s)
 if percent and unit!='percentage_points':v/=100
 return v

def score(text, case):
 hits=re.findall(r'(?im)^\s*(?:\*\*)?FINAL\s*:\s*(.+?)\s*$',text)
 result={'extracted':False,'strict_format':False,'correct':None,'parsed_final':None,'ambiguous':len(hits)>1}
 if not hits or len(hits)>1:return result
 candidate=hits[-1].strip().strip('*').strip()
 # A final marker in the final line is required for strict format; permissive extraction is separate.
 result['strict_format']=bool(re.fullmatch(r'FINAL: [+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:/[+-]?\d+)?%?',text.strip().splitlines()[-1]))
 try:
  got=scalar(candidate,case.get('answer_unit','number'))
  gold=scalar(case['reference_answer'],case.get('answer_unit','number'))
 except (ValueError,ZeroDivisionError):return result
 result.update(extracted=True,parsed_final=str(got))
 # Exact rational equivalence first; decimal rounding tolerance only for decimal responses.
 exact=(got==gold)
 tol=case.get('answer_tolerance',TOLERANCE_BY_TASK.get(case.get('task_type')))
 precision=case.get('reference_decimal_places')
 if tol is not None:rounded=abs(got-gold)<=Fraction(str(tol))
 elif precision is not None:rounded=round(float(got),precision)==round(float(gold),precision)
 else:rounded='.' in candidate and '/' not in candidate and abs(float(got-gold))<=0.00005
 result['correct']=bool(exact or rounded)
 return result

def rescore(record, cases_by_id):
 """Score a logged response with the CURRENT scoring rules; falls back to the logged score."""
 c=cases_by_id.get(record.get('case_id'))
 return score(record['response'],c) if c is not None and 'response' in record else record.get('score')
def load_cases_by_id(path):
 try:return {c['case_id']:c for c in load(path)}
 except FileNotFoundError:return {}

def is_natural(prior): return prior.startswith('natural_')
def prior_text(case, prior, model_label=None):
 if is_natural(prior): return case['natural_priors'][model_label][prior.split('_',1)[1]]
 return case[prior+'_prior']
def instruction(case, strategy): return STRATEGIES[strategy].format(hint=case.get('hint') or '')+'\n'+FINAL
def strategies_for(prior): return NATURAL_STRATEGIES if is_natural(prior) else list(STRATEGIES)

def messages(case, prior, strategy, model_label=None):
 return [{'role':'system','content':SYSTEM},{'role':'user','content':case['question']},
         {'role':'assistant','content':prior_text(case,prior,model_label)},{'role':'user','content':instruction(case,strategy)}]

def validate(cases, full=False):
 if len({c['case_id'] for c in cases})!=len(cases):raise ValueError('Duplicate case IDs')
 for c in cases:
  scalar(c['reference_answer'],c.get('answer_unit','number'))
  if c.get('seed_ready'):
   if score(c['correct_prior'],c)['correct'] is not True:raise ValueError('Invalid correct prior: '+c['case_id'])
   if score(c['wrong_prior'],c)['correct'] is not False:raise ValueError('Invalid wrong prior: '+c['case_id'])
   if full and not c.get('team_reviewed'):raise ValueError('Team review of the seeded worked solution not completed: '+c['case_id'])
  elif full and not c.get('natural_priors'):raise ValueError('Case has neither seeded nor natural priors: '+c['case_id'])
  for label,pri in (c.get('natural_priors') or {}).items():
   if 'wrong' in pri and score(pri['wrong'],c)['correct'] is not False:raise ValueError('Invalid natural wrong prior: '+c['case_id']+' '+label)
   if 'correct' in pri and score(pri['correct'],c)['correct'] is not True:raise ValueError('Invalid natural correct prior: '+c['case_id']+' '+label)
  if full and not c.get('calibration_selected'):raise ValueError('Calibration selection not completed: '+c['case_id'])
 if full:
  per_model=defaultdict(int)
  for c in cases:
   for m in c.get('eligible_models') or []:per_model[m]+=1
  thin={m:n for m,n in per_model.items() if n<20}
  if not per_model or thin:raise ValueError('Full study needs at least 20 eligible cases per model; have '+json.dumps(dict(per_model)))

class Client:
 def __init__(self, model, out, context, simulate=False):
  self.m=model;self.out=out;self.context=context;self.simulate=simulate
 def call(self,msgs,meta,case=None):
  key=os.environ.get(self.m['key_env'],'')
  if not self.simulate and not key:raise RuntimeError('Missing '+self.m['key_env']+'; set it locally, do not share it.')
  payload={'model':self.m['model'],'messages':msgs,'temperature':0.4,'top_p':1.0,'max_tokens':self.m.get('max_tokens',4096),'stream':False}
  if self.m.get('reasoning_effort') is not None:payload['reasoning_effort']=self.m['reasoning_effort']
  logical=digest([self.context,meta,self.m['label']])
  for attempt in range(1,6):
   record={'schema_version':VERSION,'is_simulated':self.simulate,'logical_call_id':logical,
    'timestamp_utc':utc(),'model_label':self.m['label'],'endpoint':self.m['base_url'],
    'requested_model_revision':self.m.get('revision'),'requested_reasoning_effort':self.m.get('reasoning_effort'),
    'attempt':attempt,'is_technical_retry':attempt>1,'request':copy.deepcopy(payload),'context_hash':self.context,**meta}
   start=time.monotonic();status=None;body=b''
   try:
    if self.simulate:
     # Deliberately imperfect fixture: one strategy fails first round; all pass next round.
     gold=case['reference_answer'] if case else '2'
     answer=gold
     if case and meta.get('round')==1 and meta.get('prior_kind')=='wrong' and meta.get('strategy')=='neutral_retry':
      answer=str(scalar(gold)+1)
     raw={'id':'SIMULATED-'+logical,'model':self.m['model'],'choices':[{'message':{'content':'Fixture explanation.\nFINAL: '+answer,'reasoning_content':'SIMULATED fixture, not model reasoning'},'finish_reason':'stop'}],
      'usage':{'prompt_tokens':100,'completion_tokens':40,'completion_tokens_details':{'reasoning_tokens':20}}}
     body=json.dumps(raw).encode();status=200
    else:
     req=urllib.request.Request(self.m['base_url'].rstrip('/')+'/chat/completions',data=json.dumps(payload).encode(),
      headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
     with urllib.request.urlopen(req,timeout=1800) as resp:body=resp.read();status=resp.status
     raw=json.loads(body)
    choice=raw['choices'][0];message=choice['message'];content=message.get('content') or ''
    if not isinstance(content,str):raise ValueError('Non-text content; inspect raw response')
    usage=raw.get('usage') or {};details=usage.get('completion_tokens_details') or {}
    rt=details.get('reasoning_tokens',usage.get('reasoning_tokens'))
    ct=usage.get('completion_tokens')
    record.update(status='success',http_status=status,raw_json=raw,raw_body=body.decode(),
     response=content,reasoning_fields={k:v for k,v in message.items() if 'reason' in k or 'think' in k},
     input_tokens=usage.get('prompt_tokens'),completion_tokens=ct,reasoning_tokens=rt,
     visible_tokens=(ct-rt if isinstance(ct,int) and isinstance(rt,int) and 0<=rt<=ct else None),
     finish_reason=choice.get('finish_reason'),returned_model=raw.get('model'),system_fingerprint=raw.get('system_fingerprint'),
     direct_api_charge_usd=(0.0 if self.m.get('billing')=='ARC-no-individual-charge' else None))
    if case:record['score']=score(content,case)
    record['latency_seconds']=time.monotonic()-start;append(self.out,record);return record
   except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,ConnectionError,ValueError,KeyError,TypeError) as e:
    if isinstance(e,urllib.error.HTTPError):status=e.code;body=e.read()
    record.update(status='error',http_status=status,error_type=type(e).__name__,
     # Credentials are never in the request URL, body or recorded headers.
     error_body=body.decode(errors='replace'),latency_seconds=time.monotonic()-start)
    append(self.out,record)
    transient=status in (429,500,502,503,504) or (status is None and isinstance(e,(urllib.error.URLError,TimeoutError,ConnectionError)))
    if not transient or attempt==5:
     hint=' (check the model name in your config)' if status==404 else (' (check ARC_API_KEY in .env)' if status in (401,403) else '')
     raise RuntimeError(f"Request failed for {self.m['label']}: HTTP {status} {type(e).__name__}{hint}. "
                        f"Details in the log. Re-run the same command with the same output folder to resume.") from None
    time.sleep(min(2**attempt,30))

def chain_key(phase,case_id,label,prior,strategy,repeat): return (phase,case_id,label,prior,strategy,repeat)

def build_jobs(phase, cases, models):
 if phase=='preflight':return [(c,m,None,None,1) for m in models for c in cases[:1]]
 if phase=='calibrate':return [(c,m,None,None,r) for c in cases for m in models for r in range(1,CALIBRATION_REPEATS+1)]
 if phase=='smoke':return [(c,m,p,s,1) for c in cases for m in models for p in SEEDED_PRIORS for s in STRATEGIES]
 jobs=[]
 for c in cases:
  for m in models:
   if c.get('eligible_models') is not None and m['label'] not in c['eligible_models']:continue
   priors=list(SEEDED_PRIORS) if c.get('seed_ready') else []
   nat=(c.get('natural_priors') or {}).get(m['label'],{})
   priors+=[p for p in NATURAL_PRIORS if p.split('_',1)[1] in nat]
   jobs+=[(c,m,p,s,r) for p in priors for s in strategies_for(p) for r in range(1,4)]
 return jobs

def run(args):
 config=load(args.config);cases=load(args.cases);validate(cases,args.phase=='study')
 out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
 if args.phase=='smoke':
  cases=[c for c in cases if c.get('seed_ready')][:3]
  if len(cases)!=3:raise ValueError('Need 3 worked cases for smoke run')
 if not args.simulate:
  for m in config['models']:
   if not os.environ.get(m['key_env']):raise RuntimeError('Missing '+m['key_env']+'; no requests sent. Put it in .env (see .env.example).')
 log=out/('SIMULATED_calls.jsonl' if args.simulate else 'calls.jsonl')
 code_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
 # What a model sees or how it is called. Scoring/analysis code may change between sessions of one
 # run (responses are rescored later); that is recorded below, never silently mixed.
 fingerprint={'phase':args.phase,'is_simulated':args.simulate,'config':config,'cases_hash':digest(cases),
              'prompt_version':VERSION,'prompts_sha256':digest([SYSTEM,FINAL,STRATEGIES,CALIBRATION_REPEATS,WRONG_ROUNDS])}
 manifest_path=out/'run_manifest.json'
 if manifest_path.exists() or log.exists():
  if not manifest_path.exists():raise ValueError('Output folder has a log but no manifest; use a new output folder.')
  old=load(manifest_path)
  changed=[k for k in fingerprint if k in old and old[k]!=fingerprint[k]]
  if changed:raise ValueError('This output folder holds a different run (changed: '+', '.join(changed)+'). Use a new output folder: never mix runs.')
  run_id=old['run_id'];resuming=True
  if old.get('code_sha256')!=code_hash:
   old.setdefault('resumed_with_other_code',[]).append({'utc':utc(),'code_sha256':code_hash});save(manifest_path,old)
 else:
  run_id=utc().replace(':','-');resuming=False
  save(manifest_path,{'created_utc':utc(),'run_id':run_id,**fingerprint,'code_sha256':code_hash,
   'python':sys.version,'platform':platform.platform(),'argv_without_secrets':sys.argv,
   'notes':'No actual LLM evidence if is_simulated=true.'})
 context=digest([config,cases,VERSION,args.phase,run_id])
 if log.exists() and _retry_io(log.read_bytes)[-1:] not in (b'',b'\n'):
  def seal():
   with log.open('a',encoding='utf-8') as f:f.write('\n')   # seal a line cut off by a crash
  with LOCK:_retry_io(seal)
 done=defaultdict(list)
 if log.exists():
  for x in read_log(log):
   if x.get('status')=='success':done[chain_key(x.get('phase'),x['case_id'],x['model_label'],x.get('prior_kind'),x.get('strategy'),x['repeat'])].append(x)
 jobs=build_jobs(args.phase,cases,config['models'])
 random.Random(20261011).shuffle(jobs)
 skipped=[0];progress=[0];total=len(jobs);stop=threading.Event()

 def do(job):
  if stop.is_set():return
  c,m,p,s,r=job;client=Client(m,log,context,args.simulate)
  key=chain_key(args.phase,c['case_id'],m['label'],p,s,r);prev=sorted(done.get(key,[]),key=lambda x:x['round'])
  if p is None:
   if prev:skipped[0]+=1
   else:
    msg=[{'role':'system','content':SYSTEM},{'role':'user','content':c['question']+'\n'+FINAL}]
    client.call(msg,{'phase':args.phase,'case_id':c['case_id'],'task_type':c['task_type'],'tier':c.get('tier'),'source':c.get('source'),'repeat':r,'round':0},c)
  else:
   limit=2 if args.phase=='smoke' else (WRONG_ROUNDS if p.endswith('wrong') else 1)
   msg=messages(c,p,s,m['label']);start=1
   if prev:
    last=prev[-1]
    if last['round']>=limit or (args.phase!='smoke' and last['score']['correct'] is True):skipped[0]+=1;msg=None
    else:   # continue a chain that was interrupted mid-way, with its exact history
     msg=last['request']['messages']+[{'role':'assistant','content':last['response']},{'role':'user','content':instruction(c,s)}];start=last['round']+1
   if msg is not None:
    for round_id in range(start,limit+1):
     rec=client.call(copy.deepcopy(msg),{'phase':args.phase,'case_id':c['case_id'],'task_type':c['task_type'],
      'tier':c.get('tier'),'source':c.get('source'),'prior_kind':p,'prior_source':'natural' if is_natural(p) else 'seeded',
      'strategy':s,'repeat':r,'round':round_id,
      'seed_error_type':None if is_natural(p) else c.get('error_type'),
      'seed_error_location':None if is_natural(p) else c.get('error_location')},c)
     if args.phase!='smoke' and rec['score']['correct'] is True:break
     msg+=[{'role':'assistant','content':rec['response']},{'role':'user','content':instruction(c,s)}]
  with LOCK:
   progress[0]+=1
   if progress[0]%50==0 or progress[0]==total:print('Completed',progress[0],'/',total,'jobs; simulated=',args.simulate,flush=True)

 if resuming:print('Resuming run',run_id,'in',out,flush=True)
 workers=max(1,args.workers)
 with ThreadPoolExecutor(max_workers=workers) as ex:
  futures=[ex.submit(do,j) for j in jobs]
  finished,_=wait(futures,return_when=FIRST_EXCEPTION)
  failed=[f for f in finished if f.exception()]
  if failed:
   stop.set();ex.shutdown(wait=True,cancel_futures=True)
   raise failed[0].exception()
 if skipped[0]:print('Skipped',skipped[0],'jobs already finished in an earlier attempt')
 print('Saved',log)

def export_audits(args):
 records=read_log(args.log)
 records=[x for x in records if x['status']=='success' and 'score' in x and x.get('phase') in ('smoke','study')]
 random.Random(20261011).shuffle(records)
 with Path(args.out).open('w',newline='',encoding='utf-8') as f:
  w=csv.writer(f);w.writerow(['logical_call_id','case_id','prior_kind','response','parser_answer','parser_correct','human_final','human_correct','explanation_rater_1','explanation_rater_2','diagnosis_rater_1','diagnosis_rater_2','visible_verification','invented_error_on_correct_prior','rater_1_id','rater_2_id','notes'])
  for x in records[:50]:w.writerow([x['logical_call_id'],x['case_id'],x.get('prior_kind'),x['response'],x['score']['parsed_final'],x['score']['correct']]+['']*11)
 print('Exported',min(50,len(records)),'audit rows; human scores left blank')

def calibrate_select(args):
 records=read_log(args.log)
 if any(r.get('is_simulated') for r in records):raise ValueError('Cannot select final cases using simulated calibration')
 lo,hi=args.band
 groups=defaultdict(dict)
 for r in records:
  if r['status']=='success' and r.get('phase')=='calibrate':groups[(r['case_id'],r['model_label'])][r['repeat']]=r
 models=[m['label'] for m in load(args.config)['models']];cases=load(args.cases);report=[]
 eligible=defaultdict(list);changed_scores=0
 for c in cases:
  rates={};nat={}
  for m in models:
   recs=[groups[(c['case_id'],m)][k] for k in sorted(groups[(c['case_id'],m)])]
   if len(recs)!=CALIBRATION_REPEATS:rates[m]=None;continue
   for x in recs:   # current scoring rules; the logged score is kept untouched in the raw log
    new=score(x['response'],c);changed_scores+=new['correct']!=x['score']['correct'];x['score']=new
   rates[m]=sum(x['score']['correct'] is True for x in recs)/CALIBRATION_REPEATS
   wrong=[x for x in recs if x['score']['correct'] is False];right=[x for x in recs if x['score']['correct'] is True]
   nat[m]={}
   if wrong:nat[m].update(wrong=wrong[0]['response'],wrong_logical_call_id=wrong[0]['logical_call_id'],wrong_parsed=wrong[0]['score']['parsed_final'])
   if right:nat[m].update(correct=right[0]['response'],correct_logical_call_id=right[0]['logical_call_id'])
   if rates[m] is not None and lo<=rates[m]<=hi:eligible[m].append(c['case_id'])
  report.append({'case_id':c['case_id'],'source':c.get('source'),'tier':c.get('tier'),'task_type':c.get('task_type'),'rates':rates})
  c['_nat']=nat;c['_rates']=rates
 # Cap each model's eligible set reproducibly so one model cannot dominate the run.
 rng=random.Random(20261011);capped={}
 for m in models:
  ids=sorted(eligible[m])
  if len(ids)>args.max_per_model:ids=sorted(rng.sample(ids,args.max_per_model));capped[m]=len(eligible[m])
  eligible[m]=set(ids)
 selected=[]
 for c in cases:
  nat,rates=c.pop('_nat'),c.pop('_rates')
  ok=[m for m in models if c['case_id'] in eligible[m]]
  if ok:
   c['calibration_selected']=True;c['calibration_rates']=rates;c['eligible_models']=ok
   c['natural_priors']={m:nat[m] for m in ok}
   selected.append(c)
 for row in report:row['eligible_models']=[m for m in models if row['case_id'] in eligible[m]]
 summary={'band':[lo,hi],'per_model_eligible':{m:len(eligible[m]) for m in models},'capped_from':capped,
  'responses_rescored_differently_from_log':changed_scores,
  'eligible_for_all_models':sum(1 for r in report if len(r['eligible_models'])==len(models)),
  'mean_accuracy_by_source':{m:{s:round(sum(v)/len(v),3) for s,v in _by(report,m).items()} for m in models}}
 save(args.out,selected);save(str(args.out)+'.calibration.json',{'summary':summary,'cases':report})
 print('Band',lo,'-',hi,'| eligible per model:',summary['per_model_eligible'],'| eligible for every model:',summary['eligible_for_all_models'])
 print('Rescored with current rules:',changed_scores,'responses changed verdict vs. the score logged at call time')
 for m in models:print(' ',m,'mean cold accuracy by source:',summary['mean_accuracy_by_source'][m])
 print('Selected',len(selected),'cases ->',args.out,'. Record any band change in deviations.csv; add candidates if a model has fewer than 20.')

def _by(report,m):
 d=defaultdict(list)
 for r in report:
  if r['rates'].get(m) is not None:d[r['source']].append(r['rates'][m])
 return d

def bootstrap(values,seed=20261011):
 if not values:return None
 rng=random.Random(seed);n=len(values)
 sims=sorted(sum(rng.choices(values,k=n))/n for _ in range(2000))
 return {'estimate':sum(values)/n,'ci95':[sims[49],sims[1949]],'cases':n}

def analyze(args):
 rows=read_log(args.log)
 if any(x.get('is_simulated') for x in rows) and not args.allow_simulated:raise ValueError('Simulation is not study evidence')
 if any(x['status']=='error' for x in rows):
  succeeded={x['logical_call_id'] for x in rows if x['status']=='success'}
  if any(x['logical_call_id'] not in succeeded for x in rows):raise ValueError('Unresolved technical failures: incomplete run; resume it before analysis, do not score as wrong')
 rows=[x for x in rows if x['status']=='success' and x.get('phase') in ('smoke','study')]
 cases_by_id=load_cases_by_id(args.cases)
 for x in rows:x['score']=rescore(x,cases_by_id)   # current scoring rules; raw log untouched
 g=defaultdict(list);chains=defaultdict(list)
 for x in rows:
  chains[(x['model_label'],x['strategy'],x['prior_kind'],x['case_id'],x['repeat'])].append(x)
  if x['round']==1:g[(x['model_label'],x['strategy'],x['prior_kind'])].append(x)
 report={'is_simulated':any(x.get('is_simulated') for x in rows),'rates':[],'paired_mcnemar_repeat1':[],'rounds':[]}
 for (model,strategy,prior),items in g.items():
  per_case=defaultdict(list);bad=defaultdict(list);missing=defaultdict(list)
  for x in items:
   s=x['score'];per_case[x['case_id']].append(s['correct'] is True)
   bad[x['case_id']].append(s['correct'] is False);missing[x['case_id']].append(s['correct'] is None)
  entry={'model':model,'strategy':strategy,'prior':prior,'first_round_end_to_end_accuracy':bootstrap([sum(v)/len(v) for v in per_case.values()]),
   'confirmed_wrong_rate':bootstrap([sum(v)/len(v) for v in bad.values()]),'unscorable_rate':bootstrap([sum(v)/len(v) for v in missing.values()])}
  if prior.endswith('correct'):entry['false_correction_upper_bound']=bootstrap([(sum(bad[k])+sum(missing[k]))/len(per_case[k]) for k in per_case])
  report['rates'].append(entry)
 # McNemar uses the predeclared first independent repeat only; multiple repeats are not independent cases.
 first={(x['model_label'],x['prior_kind'],x['strategy'],x['case_id']):x['score']['correct'] is True for x in rows if x['round']==1 and x['repeat']==1}
 for model,prior in sorted({(x['model_label'],x['prior_kind']) for x in rows}):
  for a,b in itertools.combinations(STRATEGIES,2):
   ids={k[3] for k in first if k[:3]==(model,prior,a)}&{k[3] for k in first if k[:3]==(model,prior,b)}
   if not ids:continue   # e.g. targeted_fix is not run on natural priors
   n01=sum(not first[model,prior,a,i] and first[model,prior,b,i] for i in ids)
   n10=sum(first[model,prior,a,i] and not first[model,prior,b,i] for i in ids);n=n01+n10
   p=min(1.,2*sum(math.comb(n,j) for j in range(min(n01,n10)+1))/(2**n)) if n else 1.
   report['paired_mcnemar_repeat1'].append({'model':model,'prior':prior,'a':a,'b':b,'cases':len(ids),'n01':n01,'n10':n10,'p_raw':p})
 # Holm adjustment within model/prior family (10 comparisons seeded, 6 natural).
 for model,prior in sorted({(x['model'],x['prior']) for x in report['paired_mcnemar_repeat1']}):
  family=sorted([x for x in report['paired_mcnemar_repeat1'] if x['model']==model and x['prior']==prior],key=lambda x:x['p_raw']);last=0
  for j,x in enumerate(family):last=max(last,min(1,(len(family)-j)*x['p_raw']));x['p_holm']=last
 for model,strategy,prior in sorted({(k[0],k[1],k[2]) for k in chains if k[2].endswith('wrong')}):
  subset=[v for k,v in chains.items() if k[:3]==(model,strategy,prior)]
  previous=0;previous_tokens=0;previous_calls=0
  for r in range(1,WRONG_ROUNDS+1):
   case_success=defaultdict(list);calls=0;tokens=0;missing_usage=False
   for chain in subset:
    done=[x for x in chain if x['round']<=r];success=any(x['score']['correct'] is True for x in done)
    case_success[chain[0]['case_id']].append(success);calls+=len(done)
    for x in done:
     if x['input_tokens'] is None or x['completion_tokens'] is None:missing_usage=True
     else:tokens+=x['input_tokens']+x['completion_tokens']
   count=sum(sum(v) for v in case_success.values());extra=count-previous
   report['rounds'].append({'model':model,'strategy':strategy,'prior':prior,'round':r,'cumulative_correction':bootstrap([sum(v)/len(v) for v in case_success.values()]),
    'cumulative_calls':calls,'cumulative_total_tokens':None if missing_usage else tokens,'additional_calls':calls-previous_calls,
    'additional_tokens':None if missing_usage else tokens-previous_tokens,
    'tokens_per_additional_corrected_chain':None if missing_usage or extra==0 else (tokens-previous_tokens)/extra,
    'additional_corrected_chains_since_previous_round':extra})
   previous=count;previous_tokens=tokens;previous_calls=calls
 report['task_type_rates']=[]
 tg=defaultdict(lambda:defaultdict(list))
 for x in rows:
  if x['round']==1:tg[(x['model_label'],x['strategy'],x['prior_kind'],x['task_type'])][x['case_id']].append(x['score']['correct'] is True)
 for (m,s,p,t),bycase in tg.items():report['task_type_rates'].append({'model':m,'strategy':s,'prior':p,'task_type':t,'accuracy':bootstrap([sum(v)/len(v) for v in bycase.values()])})
 save(args.out,report);print('Saved analysis; simulated=',report['is_simulated'])

def kappa(args):
 rows=list(csv.DictReader(Path(args.audit).open(encoding='utf-8')));out={}
 for field in ['explanation','diagnosis']:
  pairs=[(r[field+'_rater_1'],r[field+'_rater_2']) for r in rows if r[field+'_rater_1'] and r[field+'_rater_2']]
  n=len(pairs)
  if not n:out[field]={'n':0,'kappa':None};continue
  cats={x for pair in pairs for x in pair};po=sum(a==b for a,b in pairs)/n
  pe=sum(sum(a==c for a,b in pairs)*sum(b==c for a,b in pairs)/n**2 for c in cats)
  out[field]={'n':n,'agreement':po,'kappa':(po-pe)/(1-pe) if pe<1 else None}
 save(args.out,out);print(out)

def main():
 ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='command',required=True)
 p=sub.add_parser('run');p.add_argument('--phase',choices=['preflight','calibrate','smoke','study'],required=True)
 p.add_argument('--config',default='models.json');p.add_argument('--cases',default='cases_candidates.json');p.add_argument('--out',required=True);p.add_argument('--simulate',action='store_true')
 p.add_argument('--workers',type=int,default=4,help='parallel requests (lower it if ARC returns many 429s)');p.set_defaults(fn=run)
 p=sub.add_parser('select');p.add_argument('--log',required=True);p.add_argument('--cases',default='cases_candidates.json');p.add_argument('--config',default='models.json');p.add_argument('--out',required=True)
 p.add_argument('--band',type=float,nargs=2,default=[0.3,0.7],metavar=('LOW','HIGH'));p.add_argument('--max-per-model',type=int,default=50);p.set_defaults(fn=calibrate_select)
 p=sub.add_parser('audit');p.add_argument('--log',required=True);p.add_argument('--out',required=True);p.set_defaults(fn=export_audits)
 p=sub.add_parser('analyze');p.add_argument('--log',required=True);p.add_argument('--out',required=True);p.add_argument('--allow-simulated',action='store_true')
 p.add_argument('--cases',default='cases_candidates.json',help='used to rescore responses with the current scoring rules');p.set_defaults(fn=analyze)
 p=sub.add_parser('kappa');p.add_argument('--audit',required=True);p.add_argument('--out',required=True);p.set_defaults(fn=kappa)
 args=ap.parse_args()
 try:args.fn(args)
 except (ValueError,RuntimeError) as e:ap.exit(2,str(e)+'\n')
if __name__=='__main__':main()
