"""Simulated reader on a previously recorded Codex draft; no new inference calls."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'workbench/tools'))
from verify_review_mvp import SessionServer, snapshot_source, free_port, stop
from workbench.review.schemas import Request, validate_draft
from workbench.review.provenance import source_snapshot, environment, object_hash
from workbench.review import render
from workbench.review_app import create_app


class RecordedDraft:
    name = 'recorded-codex-SG001-replay-v1'
    live = False

    def __init__(self, input_dir):
        self.draft = json.loads((input_dir / 'draft.json').read_text())
        self.request = json.loads((input_dir / 'input.json').read_text())

    def generate(self, operation, request, draft=None, issues=None, **kwargs):
        if operation != 'draft' or request['sources'] != self.request['sources']:
            raise ValueError('This replay permits only the recorded passage and one draft operation')
        value = deepcopy(self.draft)
        value['provider'] = self.name
        value['provenance'] = 'Replay of the one real SG001 Codex output; no model called here. All subsequent reader annotations are assistant-simulated.'
        return value


def prepare(input_dir):
    provider = RecordedDraft(input_dir)
    request = Request.model_validate({**provider.request, 'workflow_mode':'single_draft'}).model_dump(mode='json')
    draft = provider.generate('draft', request)
    reading = draft['readings'][0]
    text = request['sources'][0]['text']
    for node in reading['nodes']:
        quote = node['content'].split('“', 1)[1].split('”', 1)[0]
        assert text.count(quote) == 1, (node['id'],quote)
        start = text.index(quote)
        node['source_refs'] = [{'source_id':'main','start':start,'end':start+len(quote),'quote':quote}]
        node['rationale'] += ' Simulated reader: exact citation added; inference edge remains a claim to test.'
    scope = {'mode':'declared_formulas', 'rationale':'SIMULATED reader: check only the supplied closed classical formulas. Material implication does not encode the original counterfactual, reference or meaningfulness. Retain those qualifications; no verdict on the entire passage.'}
    reading['formal_scope'] = deepcopy(scope)
    by_id = {n['id']: n for n in reading['nodes']}
    sense = {
        'id':'sense-check', 'summary':'Simulated local question: does the hypothetical existence of a unique mental Pegasus-idea entail a physical Pegasus? This tested connection is not attributed to Quine.',
        'nodes':[deepcopy(by_id['n5']), deepcopy(by_id['n3'])], 'target':'n3',
        'formal_mapping':{'semantics':'classical-first-order-v1', 'formulas':{'n5':'exists x. (M(x) & (forall y. (M(y) -> y = x)))','n3':'exists x. H(x)'},
                          'rationale':'M means mental Pegasus-idea; H means physical Pegasus. Distinct predicates, no bridge. n5 is Quine\'s temporary concession. n3 is a reader-selected physical interpretation of the reported conclusion, not a Quine assertion.'},
        'qualifications':deepcopy(reading['qualifications']), 'formal_scope':deepcopy(scope),
    }
    sense['nodes'][1].update(content='Candidate physical conclusion under test: a physical Pegasus exists.', speaker='SIMULATED reader: selected interpretation', commitment='hypothetical', depends_on=['n5'], rationale='Test the physical reading without identifying a mental object with a physical horse; no claim of Quine endorsement.')
    draft['readings'].append(sense)
    draft['summary'] = 'SIMULATED USER ANNOTATIONS. Exact citations supplied; separate reported chain and mental-versus-physical test. Original unencoded qualifications retained. Text interpretation remains disputed.'
    cases = [{'id':'distinct-senses','draft':deepcopy(draft),'expected':['blocked','witness_found']}]
    conditional = deepcopy(draft)
    chain = conditional['readings'][0]
    assumptions = [
        ('S1','Every relevant denial is a relevant use of the word Pegasus.','forall x. (D(x) -> T(x))','Classification bridge between denial utterances and relevant uses; reconstructor-supplied, not quoted.'),
        ('S2','Any relevant utterance that fails to talk about something is nonsensical.','forall x. (~A(x) -> N(x))','Controversial semantic bridge. Added explicitly for a conditional test, not accepted as true or as Quine\'s position.'),
        ('S3','At least one relevant denial is meaningful.','exists x. (D(x) & ~N(x))','Additional witness excluding universal nonsense. Not established as McX\'s premise; supplied only to test sufficiency.'),
    ]
    for key,content,formula,rationale in assumptions:
        chain['nodes'].append({'id':key,'content':content,'role':'premise','speaker':'SIMULATED reader: conditional sensitivity test','commitment':'proposed_assumption','source_refs':[],'depends_on':[],'rationale':rationale})
        chain['formal_mapping']['formulas'][key]=formula
    next(n for n in chain['nodes'] if n['id']=='n2')['depends_on']=['n1','S1','S2']
    next(n for n in chain['nodes'] if n['id']=='n3')['depends_on']=['n2','S3']
    chain['summary']='Conditional sensitivity test with THREE extra premises. A valid result here does not repair or validate the unaugmented source argument.'
    chain['formal_mapping']['rationale'] += ' S1/S2/S3 are explicit extra premises. The solver must test consistency as well as entailment.'
    for q in chain['qualifications']:
        if q['kind']=='validity':
            q['description']='The original graph lacked semantic bridges; this variant explicitly adds S1/S2/S3 and asks only whether they suffice.'
            q['rationale']='Conditional hypothesis experiment, not a claim that the author supplied or accepted those premises.'
    cases.append({'id':'explicit-extra-premises','draft':conditional,'expected':['no_counterexample_complete','witness_found']})
    merged=deepcopy(draft)
    merged['readings'][1]['formal_mapping']['formulas']['n3']='exists x. M(x)'
    merged['readings'][1]['formal_mapping']['rationale'] += ' NEGATIVE CONTROL: H has deliberately been conflated with M. A valid result under this changed formula is misleading for the physical conclusion.'
    merged['readings'][1]['summary']='SIMULATED NEGATIVE CONTROL: mental and physical senses are deliberately conflated to show why the reader must distinguish them.'
    cases.append({'id':'conflated-sense-control','draft':merged,'expected':['blocked','no_counterexample_complete']})
    for case in cases:
        case['draft'], issues=validate_draft(case['draft'],request)
        assert not any(i['kind']=='attribution_without_reference' for i in issues)
        case['issues']=issues
    return request,cases


def main(args):
    if args.serve:
        import uvicorn
        app=create_app(args.output/'data',args.kv_port,namespace='simulated-scope',provider=RecordedDraft(args.input_dir),run_source_worker=False)
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')
        return 0
    out=args.output
    out.mkdir(parents=True,exist_ok=False)
    request,cases=prepare(args.input_dir)
    (out/'cases.json').write_text(json.dumps({'request':request,'cases':cases},ensure_ascii=False,indent=2))
    before=source_snapshot(ROOT)
    provenance={'source':before,'environment':environment(),'input_draft_sha256':object_hash(json.loads((args.input_dir/'draft.json').read_text())),
                'reviewer':'assistant-simulated user, explicitly authorized; not user or expert approval','model_calls_this_run':0,'original_model_calls':1,'source_record':'SG001','cases_sha256':object_hash({'request':request,'cases':cases})}
    (out/'provenance.json').write_text(json.dumps(provenance,indent=2))
    session=SessionServer(args.kv_root,out/'session.aof')
    server=None;logs=[];result={}
    try:
        session.start()
        def launch():
            port=free_port()
            log=(out/f'app-{len(logs)}.log').open('wb');logs.append(log)
            process=subprocess.Popen([sys.executable,__file__,'--serve','--input-dir',str(args.input_dir),'--output',str(out),'--kv-root',str(args.kv_root),'--kv-port',str(session.port),'--port',str(port)],cwd=ROOT,stdout=log,stderr=log)
            for _ in range(200):
                try:
                    with urlopen(f'http://127.0.0.1:{port}/api/argument-health',timeout=.2) as response:
                        assert json.load(response)['kv_available']
                    return process,f'http://127.0.0.1:{port}'
                except OSError:
                    if process.poll() is not None: raise RuntimeError('App stopped; inspect logs')
                    time.sleep(.05)
            stop(process);raise RuntimeError('Startup timeout')
        server,base=launch()
        env={**os.environ,'REVIEW_TEST_URL':base,'REVIEW_ARTIFACTS':str(out)}
        completed=subprocess.run(['node',str(ROOT/'workbench/tests/browser_scoped_review.cjs')],env=env,capture_output=True,text=True,timeout=150)
        (out/'browser.log').write_text(completed.stdout+completed.stderr)
        if completed.returncode: raise RuntimeError(completed.stderr)
        result['browser']=json.loads(completed.stdout)
        stop(server);server=None
        server,base=launch()
        checks=[]
        for case in result['browser']['cases']:
            run_id=case['run_id']
            with urlopen(f'{base}/api/argument-runs/{run_id}') as response: view=json.load(response)
            assert view['status']=='succeeded'
            assert len([e for e in view['events'] if e['node']=='model_call'])==1
            with urlopen(f'{base}/api/argument-runs/{run_id}/report.json') as response: packet=json.load(response)
            verification=render.verify(packet)
            assert packet['report']['tool_version']=='review-tools-v3'
            checks.append({'id':run_id,'packet':verification['status'],'versions':len(view['versions']),'repeated_generation':False})
        result.update(status='passed',restart=checks)
    except Exception as error:
        result.update(status='failed',error=str(error))
    finally:
        stop(server);session.stop()
        for log in logs: log.close()
    result.update(source_unchanged=source_snapshot(ROOT)['sha256']==before['sha256'],model_calls_this_run=0,original_model_calls=1,reviewer='assistant-simulated user')
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='passed' and result['source_unchanged'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--kv-root',type=Path,required=True)
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--port',type=int)
    parser.add_argument('--kv-port',type=int)
    raise SystemExit(main(parser.parse_args()))
