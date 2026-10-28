#!/usr/bin/env python3
"""ARC correction study: standard-library HTTP, explicit logs, no credentials in files.
Simulation is a software check only. It is never research evidence.
"""
import argparse, ast, copy, csv, hashlib, itertools, json, math, os
import platform, random, re, sys, time, urllib.error, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

VERSION = 'part1-1.0'
SYSTEM = 'You are a mathematical problem solver. Evaluate the supplied solution accurately.'
FINAL = 'Give a concise explanation and end with exactly one line: FINAL: <numeric answer>. Use a decimal, fraction, or percentage; do not add units or approximation text to that line.'
STRATEGIES = {
 'neutral_retry': 'Revisit the original question and give your own final answer.',
 'error_flagging': 'The previous answer may be incorrect. Check it carefully and correct it if needed.',
 'targeted_fix': 'Check this possible source of error: {hint}. This is a check, not a claim that an error occurred. Correct the solution only if needed.',
 'step_by_step_redo': 'Solve the question again from the beginning, showing the key calculation steps. Then provide the final answer.',
 'self_check': 'Solve independently, then verify the result using a separate check before giving the final answer.'
}

def utc(): return datetime.now(timezone.utc).isoformat()
def digest(x): return hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()
def load(p): return json.loads(Path(p).read_text())
def save(p, x): Path(p).write_text(json.dumps(x, indent=2, ensure_ascii=False))
def append(p, x):
 with Path(p).open('a') as f: f.write(json.dumps(x, ensure_ascii=False)+'\n')

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
 precision=case.get('reference_decimal_places')
 rounded=(round(float(got),precision)==round(float(gold),precision)) if precision is not None else ('.' in candidate and '/' not in candidate and abs(float(got-gold))<=0.00005)
 result['correct']=bool(exact or rounded)
 return result

def messages(case, prior, strategy):
 instruction=STRATEGIES[strategy].format(hint=case['hint'])+'\n'+FINAL
 return [{'role':'system','content':SYSTEM},{'role':'user','content':case['question']},
         {'role':'assistant','content':case[prior+'_prior']},{'role':'user','content':instruction}]

def validate(cases, full=False):
 if len({c['case_id'] for c in cases})!=len(cases):raise ValueError('Duplicate case IDs')
 for c in cases:
  scalar(c['reference_answer'],c.get('answer_unit','number'))
  if c.get('seed_ready'):
   if score(c['correct_prior'],c)['correct'] is not True:raise ValueError('Invalid correct prior: '+c['case_id'])
   if score(c['wrong_prior'],c)['correct'] is not False:raise ValueError('Invalid wrong prior: '+c['case_id'])
  elif full:raise ValueError('Finish and review worked seed for '+c['case_id'])
  if full and (not c.get('calibration_selected') or not c.get('team_reviewed')):
   raise ValueError('Calibration and team review not completed: '+c['case_id'])
 if full and not 40<=len(cases)<=50:raise ValueError('Full study requires 40–50 selected cases')

class Client:
 def __init__(self, model, out, context, simulate=False):
  self.m=model;self.out=out;self.context=context;self.simulate=simulate
 def call(self,msgs,meta,case=None):
  key=os.environ.get(self.m['key_env'],'')
  if not self.simulate and not key:raise RuntimeError('Missing '+self.m['key_env']+'; set it locally, do not share it.')
  payload={'model':self.m['model'],'messages':msgs,'temperature':0.4,'top_p':1.0,'max_tokens':4096,'stream':False}
  if self.m.get('reasoning_effort') is not None:payload['reasoning_effort']=self.m['reasoning_effort']
  logical=digest([self.context,meta,self.m['label']])
  for attempt in range(1,4):
   record={'schema_version':VERSION,'is_simulated':self.simulate,'logical_call_id':logical,
    'timestamp_utc':utc(),'model_label':self.m['label'],'endpoint':self.m['base_url'],
    'requested_model_revision':self.m.get('revision'),'requested_reasoning_effort':self.m.get('reasoning_effort'),
    'attempt':attempt,'is_technical_retry':attempt>1,'request':payload,'context_hash':self.context,**meta}
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
   except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,ValueError,KeyError,TypeError) as e:
    if isinstance(e,urllib.error.HTTPError):status=e.code;body=e.read()
    record.update(status='error',http_status=status,error_type=type(e).__name__,
     # Credentials are never in the request URL, body or recorded headers.
     error_body=body.decode(errors='replace'),latency_seconds=time.monotonic()-start)
    append(self.out,record)
    if status not in (429,500,502,503,504) or attempt==3:raise RuntimeError('Request failed; see logged status') from None
    time.sleep(min(2**attempt,8))

def run(args):
 config=load(args.config);cases=load(args.cases);validate(cases,args.phase=='study')
 out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
 if args.phase=='smoke':
  cases=[c for c in cases if c.get('seed_ready')][:3]
  if len(cases)!=3:raise ValueError('Need 3 worked cases for smoke run')
 live=not args.simulate
 if live:
  for m in config['models']:
   if not os.environ.get(m['key_env']):raise RuntimeError('Missing '+m['key_env']+'; no requests sent')
 run_id=utc().replace(':','-');context=digest([config,cases,VERSION,args.phase,run_id])
 log=out/('SIMULATED_calls.jsonl' if args.simulate else 'calls.jsonl')
 if log.exists():raise ValueError('Use a new output directory: never overwrite or mix runs')
 save(out/'run_manifest.json',{'created_utc':utc(),'phase':args.phase,'is_simulated':args.simulate,
  'config':config,'cases_hash':digest(cases),'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
  'python':sys.version,'platform':platform.platform(),'prompt_version':VERSION,'argv_without_secrets':sys.argv,
  'notes':'No actual LLM evidence if is_simulated=true.'})
 jobs=[]
 if args.phase=='preflight':jobs=[(c,m,None,None,1) for m in config['models'] for c in cases[:1]]
 elif args.phase=='calibrate':jobs=[(c,m,None,None,r) for c in cases for m in config['models'] for r in range(1,6)]
 else:
  repeats=1 if args.phase=='smoke' else 3
  jobs=[(c,m,p,s,r) for c in cases for m in config['models'] for p in ['wrong','correct'] for s in STRATEGIES for r in range(1,repeats+1)]
 random.Random(20261011).shuffle(jobs)
 for i,(c,m,p,s,r) in enumerate(jobs,1):
  client=Client(m,log,context,args.simulate)
  if p is None:
   msg=[{'role':'system','content':SYSTEM},{'role':'user','content':c['question']+'\n'+FINAL}]
   client.call(msg,{'phase':args.phase,'case_id':c['case_id'],'task_type':c['task_type'],'repeat':r,'round':0},c)
  else:
   msg=messages(c,p,s)
   limit=2 if args.phase=='smoke' else (3 if p=='wrong' else 1)
   for round_id in range(1,limit+1):
    rec=client.call(msg,{'phase':args.phase,'case_id':c['case_id'],'task_type':c['task_type'],
     'tier':c['tier'],'prior_kind':p,'strategy':s,'repeat':r,'round':round_id,'seed_error_type':c['error_type'],
     'seed_error_location':c['error_location']},c)
    if args.phase!='smoke' and rec['score']['correct'] is True:break
    msg+= [{'role':'assistant','content':rec['response']},{'role':'user','content':STRATEGIES[s].format(hint=c['hint'])+'\n'+FINAL}]
  if i%10==0 or i==len(jobs):print('Completed',i,'/',len(jobs),'chains; simulated=',args.simulate,flush=True)
 print('Saved',log)

def export_audits(args):
 records=[json.loads(x) for x in Path(args.log).read_text().splitlines()]
 records=[x for x in records if x['status']=='success' and 'score' in x]
 random.Random(20261011).shuffle(records)
 with Path(args.out).open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['logical_call_id','case_id','response','parser_answer','parser_correct','human_final','human_correct','explanation_rater_1','explanation_rater_2','diagnosis_rater_1','diagnosis_rater_2','visible_verification','invented_error_on_correct_prior','rater_1_id','rater_2_id','notes'])
  for x in records[:50]:w.writerow([x['logical_call_id'],x['case_id'],x['response'],x['score']['parsed_final'],x['score']['correct']]+['']*11)
 print('Exported',min(50,len(records)),'audit rows; human scores left blank')

def calibrate_select(args):
 records=[json.loads(x) for x in Path(args.log).read_text().splitlines()]
 if any(r.get('is_simulated') for r in records):raise ValueError('Cannot select final cases using simulated calibration')
 groups=defaultdict(list)
 for r in records:
  if r['status']=='success' and r.get('phase')=='calibrate':groups[(r['case_id'],r['model_label'])].append(r['score']['correct'] is True)
 models=load(args.config)['models'];selected=[];report=[]
 for c in load(args.cases):
  rates={m['label']:(sum(groups[(c['case_id'],m['label'])])/5 if len(groups[(c['case_id'],m['label'])])==5 else None) for m in models}
  keep=all(x is not None and .3<=x<=.7 for x in rates.values())
  report.append({'case_id':c['case_id'],'rates':rates,'eligible':keep})
  if keep:
   c['calibration_selected']=True;c['calibration_rates']=rates;selected.append(c)
 save(args.out,selected);save(str(args.out)+'.calibration.json',report)
 print('Eligible',len(selected),'; do not relax selection silently. Add candidates if fewer than 40.')

def bootstrap(values,seed=20261011):
 if not values:return None
 rng=random.Random(seed);n=len(values)
 sims=sorted(sum(rng.choices(values,k=n))/n for _ in range(2000))
 return {'estimate':sum(values)/n,'ci95':[sims[49],sims[1949]],'cases':n}

def analyze(args):
 rows=[json.loads(x) for x in Path(args.log).read_text().splitlines()]
 if any(x.get('is_simulated') for x in rows) and not args.allow_simulated:raise ValueError('Simulation is not study evidence')
 if any(x['status']=='error' for x in rows):
  succeeded={x['logical_call_id'] for x in rows if x['status']=='success'}
  if any(x['logical_call_id'] not in succeeded for x in rows):raise ValueError('Unresolved technical failures: incomplete run; do not score as wrong')
 rows=[x for x in rows if x['status']=='success' and x.get('phase') in ('smoke','study')]
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
  if prior=='correct':entry['false_correction_upper_bound']=bootstrap([(sum(bad[k])+sum(missing[k]))/len(per_case[k]) for k in per_case])
  report['rates'].append(entry)
 # McNemar uses the predeclared first independent repeat only; multiple repeats are not independent cases.
 first={(x['model_label'],x['prior_kind'],x['strategy'],x['case_id']):x['score']['correct'] is True for x in rows if x['round']==1 and x['repeat']==1}
 for model,prior in sorted({(x['model_label'],x['prior_kind']) for x in rows}):
  for a,b in itertools.combinations(STRATEGIES,2):
   ids={k[3] for k in first if k[:3]==(model,prior,a)}&{k[3] for k in first if k[:3]==(model,prior,b)}
   n01=sum(not first[model,prior,a,i] and first[model,prior,b,i] for i in ids)
   n10=sum(first[model,prior,a,i] and not first[model,prior,b,i] for i in ids);n=n01+n10
   p=min(1.,2*sum(math.comb(n,j) for j in range(min(n01,n10)+1))/(2**n)) if n else 1.
   report['paired_mcnemar_repeat1'].append({'model':model,'prior':prior,'a':a,'b':b,'n01':n01,'n10':n10,'p_raw':p})
 # Holm adjustment within model/prior family (10 comparisons).
 for model,prior in sorted({(x['model'],x['prior']) for x in report['paired_mcnemar_repeat1']}):
  family=sorted([x for x in report['paired_mcnemar_repeat1'] if x['model']==model and x['prior']==prior],key=lambda x:x['p_raw']);last=0
  for j,x in enumerate(family):last=max(last,min(1,(len(family)-j)*x['p_raw']));x['p_holm']=last
 for model,strategy in sorted({(k[0],k[1]) for k in chains if k[2]=='wrong'}):
  subset=[v for k,v in chains.items() if k[:3]==(model,strategy,'wrong')]
  previous=0;previous_tokens=0;previous_calls=0
  for r in range(1,4):
   case_success=defaultdict(list);calls=0;tokens=0;missing_usage=False
   for chain in subset:
    done=[x for x in chain if x['round']<=r];success=any(x['score']['correct'] is True for x in done)
    case_success[chain[0]['case_id']].append(success);calls+=len(done)
    for x in done:
     if x['input_tokens'] is None or x['completion_tokens'] is None:missing_usage=True
     else:tokens+=x['input_tokens']+x['completion_tokens']
   count=sum(sum(v) for v in case_success.values());extra=count-previous
   report['rounds'].append({'model':model,'strategy':strategy,'round':r,'cumulative_correction':bootstrap([sum(v)/len(v) for v in case_success.values()]),
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
 rows=list(csv.DictReader(Path(args.audit).open()));out={}
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
 p.add_argument('--config',default='models.json');p.add_argument('--cases',default='cases_candidates.json');p.add_argument('--out',required=True);p.add_argument('--simulate',action='store_true');p.set_defaults(fn=run)
 p=sub.add_parser('select');p.add_argument('--log',required=True);p.add_argument('--cases',default='cases_candidates.json');p.add_argument('--config',default='models.json');p.add_argument('--out',required=True);p.set_defaults(fn=calibrate_select)
 p=sub.add_parser('audit');p.add_argument('--log',required=True);p.add_argument('--out',required=True);p.set_defaults(fn=export_audits)
 p=sub.add_parser('analyze');p.add_argument('--log',required=True);p.add_argument('--out',required=True);p.add_argument('--allow-simulated',action='store_true');p.set_defaults(fn=analyze)
 p=sub.add_parser('kappa');p.add_argument('--audit',required=True);p.add_argument('--out',required=True);p.set_defaults(fn=kappa)
 args=ap.parse_args()
 try:args.fn(args)
 except (ValueError,RuntimeError) as e:ap.exit(2,str(e)+'\n')
if __name__=='__main__':main()
