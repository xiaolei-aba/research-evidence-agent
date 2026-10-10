import unittest, json, sys
from pathlib import Path
from dataclasses import asdict
root=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(root/'src'))
from deepresearch.models import *
from deepresearch.agent.workflow import Agent
from deepresearch.llm.provider import MockProvider
from deepresearch.retrieval.offline import OfflineSearchProvider
from deepresearch.memory.store import EvidenceStore
from deepresearch.tools.executor import ToolExecutor, BudgetExceeded
from deepresearch.evaluation.metrics import metrics
class B0Tests(unittest.TestCase):
    def setUp(self): self.search=OfflineSearchProvider(root/'data/fixtures/documents.json')
    def run_agent(self,q='Atlas',provider=None,**kwargs): return Agent(provider or MockProvider(),self.search,self.search,**kwargs).run(UserQuery(q))
    def test_roundtrip_all_models(self):
        d=self.search.read('atlas'); s=EvidenceStore(); e=s.add(d,'t')
        objs=[UserQuery('q'),ResearchPlan('q',[SubTask('t','q')]),SubTask('t','q'),ToolRequest('t','search','q'),ToolResult('r','ok',[d]),d,e,CriticFeedback(True,'ok',[],[]),Citation(e.evidence_id,e.content),FinalAnswer('q','x',[],'ok'),AgentTrace('q',[TraceEvent('tool','ok',1,{})])]
        for obj in objs:
            with self.subTest(model=type(obj).__name__): self.assertEqual(asdict(obj),asdict(type(obj).from_json(obj.to_json())))
    def test_planner(self): self.assertEqual(len(self.run_agent('Atlas;Cedar')['plans'][0]['tasks']),2)
    def test_invalid_json(self): self.assertEqual(self.run_agent(provider=MockProvider({'plan':['{']}))['answer']['status'],'failed')
    def test_invalid_plan(self): self.assertEqual(self.run_agent(provider=MockProvider({'plan':['{"queries": [7]}']}))['answer']['status'],'failed')
    def test_empty_answer(self): self.assertEqual(self.run_agent(provider=MockProvider({'answer':['{"text":"","citations":[]}']}))['answer']['status'],'failed')
    def test_model_exception(self): self.assertIn('boom',self.run_agent(provider=MockProvider({'plan':[RuntimeError('boom')]}))['answer']['error'])
    def test_tools(self):
        t=ToolExecutor(self.search,self.search,AgentTrace('q'))
        self.assertEqual(t.call(ToolRequest('t','search','Atlas')).status,'ok')
        self.assertEqual(t.call(ToolRequest('t','read','absent')).status,'error')
        self.assertEqual(t.call(ToolRequest('t','invalid','x')).status,'error')
    def test_search_exception(self):
        class Broken:
            def search(self,q): raise OSError('search broken')
        r=Agent(MockProvider(),Broken(),self.search,max_replans=0).run(UserQuery('Atlas'))
        self.assertTrue(any(e['error']=='search broken' for e in r['trace']['events']))
    def test_read_exception(self):
        class Broken:
            def read(self,q): raise OSError('read broken')
        r=Agent(MockProvider(),self.search,Broken(),max_replans=0).run(UserQuery('Atlas'))
        self.assertEqual(r['answer']['status'],'insufficient_evidence')
    def test_empty_search(self): self.assertEqual(self.search.search('UnknownXYZ'),[])
    def test_missing_document(self):
        with self.assertRaises(KeyError): self.search.read('absent')
    def test_dedup(self):
        s=EvidenceStore(); d=self.search.read('atlas'); s.add(d,'a'); s.add(d,'b')
        self.assertEqual(len(s.items),1); self.assertEqual(next(iter(s.items.values())).task_ids,['a','b'])
        s=EvidenceStore(False); s.add(d,'a'); s.add(d,'a'); self.assertEqual(len(s.items),2)
    def test_citation(self):
        s=EvidenceStore(); e=s.add(self.search.read('atlas'),'a'); s.validate(Citation(e.evidence_id,e.content))
        with self.assertRaises(ValueError): s.validate(Citation('fake','x'))
        with self.assertRaises(ValueError): s.validate(Citation(e.evidence_id,'unsupported'))
    def test_fabricated_citation(self):
        r=self.run_agent(provider=MockProvider({'answer':[json.dumps({'text':'claim','citations':[{'evidence_id':'fake','claim':'claim'}]})]}))
        self.assertEqual(r['answer']['status'],'failed')
    def test_critic_conflict(self): self.assertEqual(self.run_agent('Quota')['feedback'][0]['conflicts'],['quota'])
    def test_replan_limit(self): self.assertEqual(len(self.run_agent('UnknownXYZ',max_replans=2)['plans']),3)
    def test_replan_success(self):
        p=MockProvider({'critic':[json.dumps({'sufficient':False,'reason':'need more','missing_queries':['Cedar'],'conflicts':[]})]})
        r=self.run_agent(provider=p); self.assertEqual(len(r['plans']),2); self.assertEqual(r['answer']['status'],'completed')
    def test_tool_limit(self):
        r=self.run_agent(max_tool_calls=1); self.assertEqual(r['trace']['tool_calls'],1); self.assertEqual(r['answer']['status'],'failed')
    def test_cache_switch(self):
        a=self.run_agent('Atlas;Atlas',cache=True); b=self.run_agent('Atlas;Atlas',cache=False)
        self.assertEqual(a['trace']['tool_calls'],2); self.assertEqual(b['trace']['tool_calls'],4); self.assertEqual(a['cache']['hits'],2)
    def test_trace(self):
        r=self.run_agent(); self.assertGreater(r['trace']['latency_ms'],0); self.assertTrue(any(e['kind']=='tool' for e in r['trace']['events']))
        self.assertTrue(any(e['status']=='running' for e in r['trace']['events']))
    def test_end_to_end(self):
        r=self.run_agent('Atlas;Cedar'); self.assertEqual(r['answer']['status'],'completed'); self.assertIn('Harbor',r['answer']['text'])
        evidence={e['evidence_id']:e for e in r['evidence']}
        for c in r['answer']['citations']: self.assertEqual(evidence[c['evidence_id']]['source'],self.search.read(evidence[c['evidence_id']]['document_id']).source)
    def test_false_critic_sufficiency_veto(self):
        provider=MockProvider({'critic':[json.dumps({'sufficient':True,'reason':'wrong','missing_queries':[],'conflicts':[]})]})
        self.assertEqual(self.run_agent('UnknownXYZ',provider=provider,max_replans=0)['answer']['status'],'insufficient_evidence')
    def test_invalid_critic(self):
        self.assertEqual(self.run_agent(provider=MockProvider({'critic':['{"sufficient":"yes"}']}))['answer']['status'],'failed')
    def test_uncited_answer_rejected(self):
        r=self.run_agent(); e=r['evidence'][0]
        provider=MockProvider({'answer':[json.dumps({'text':'unsupported','citations':[{'evidence_id':e['evidence_id'],'claim':e['content']}]})]})
        self.assertEqual(self.run_agent(provider=provider)['answer']['status'],'failed')
    def test_zero_budget(self):
        self.assertEqual(self.run_agent(max_tool_calls=0)['trace']['tool_calls'],0)
    def test_business_output_repeatable(self):
        a=self.run_agent(); b=self.run_agent()
        self.assertEqual(a['answer']['text'],b['answer']['text'])
        self.assertEqual([c['evidence_id'] for c in a['answer']['citations']],[c['evidence_id'] for c in b['answer']['citations']])
    def test_metrics(self):
        r=self.run_agent(); m=metrics(r,True,[True]); self.assertEqual(m['citation_accuracy'],1); self.assertIsNone(m['token_usage'])
        self.assertIsNone(metrics(r)['answer_accuracy'])
    def test_budget_validation(self):
        with self.assertRaises(ValueError): self.run_agent(max_replans=-1)
if __name__=='__main__': unittest.main()
