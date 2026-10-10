"""CPU fixtures for the frozen B4 memory factor; no benchmark Gold or model calls."""
from dataclasses import asdict, replace
from pathlib import Path
from hashlib import sha256
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from deepresearch.agent.budget import ResearchLimits
from deepresearch.agent.research import ResearchEvidenceAgent
from deepresearch.benchmarks.runtime import BenchmarkCorpusReader
from deepresearch.llm.provider import MockProvider
from deepresearch.memory.store import EvidenceStore
from deepresearch.runtime.execution import configuration
from deepresearch.models import RetrievedDocument, UserQuery


def document(**changes):
    content = 'Alpha supports the example.'
    digest = sha256(content.encode()).hexdigest()
    original = RetrievedDocument(
        'doc', 'https://example.invalid/doc', 'Synthetic fixture', content, {},
        source_id='source', source_version='v1', section_id='section', chunk_id='doc:p0',
        document_sha256=digest, chunk_sha256=digest, span_start=0,
        span_end=len(content), source_text=content, license='synthetic test fixture',
    )
    return replace(original, **changes)


class SourceIdentityTests(unittest.TestCase):
    def test_repeated_source_preserves_first_item_and_all_task_associations(self):
        store = EvidenceStore(deduplicate=True)
        first = store.add(document(), '0:0')
        identity = first.evidence_id
        retrieved_at = first.retrieved_at
        for task_id in ('0:1', '1:0', '1:0'):
            self.assertIs(store.add(document(), task_id), first)
        self.assertEqual(list(store.items), [identity])
        self.assertEqual(first.task_ids, ['0:0', '0:1', '1:0'])
        self.assertEqual(first.retrieved_at, retrieved_at)
        self.assertEqual(first.locator, 'section:0:27')

    def test_equal_text_from_distinct_document_source_version_or_chunk_is_retained(self):
        for change in (
            {'document_id': 'other-doc'},
            {'source': 'https://example.invalid/other'},
            {'source_version': 'v2'},
            {'chunk_id': 'doc:p1'},
        ):
            with self.subTest(change=change):
                store = EvidenceStore(deduplicate=True)
                first = store.add(document(), 'a')
                second = store.add(document(**change), 'b')
                self.assertEqual(len(store.items), 2)
                self.assertNotEqual(first.evidence_id, second.evidence_id)
                self.assertEqual(first.content, second.content)

    def test_changed_content_is_retained_without_normalization_or_overwrite(self):
        for content in ('alpha supports the example.', 'Alpha supports the example. ',
                        'Alpha contradicts the example.'):
            with self.subTest(content=content):
                store = EvidenceStore(deduplicate=True)
                first = store.add(document(), 'a')
                second = store.add(document(content=content), 'b')
                self.assertEqual(len(store.items), 2)
                self.assertNotEqual(first.evidence_id, second.evidence_id)
                self.assertEqual(first.content, 'Alpha supports the example.')
                self.assertEqual(second.content, content)

    def test_switch_off_preserves_each_retrieval_as_independent_item(self):
        store = EvidenceStore(deduplicate=False)
        identities = [store.add(document(), task_id).evidence_id
                      for task_id in ('0:0', '0:1', '1:0')]
        self.assertEqual(len(set(identities)), 3)
        self.assertEqual(len(store.items), 3)
        self.assertEqual([item.task_ids for item in store.items.values()],
                         [['0:0'], ['0:1'], ['1:0']])

    def test_dedup_preserves_first_occurrence_order_and_source_locator(self):
        store = EvidenceStore(deduplicate=True)
        first = store.add(document(chunk_id='doc:p1'), 'a')
        second = store.add(document(chunk_id='doc:p0'), 'a')
        store.add(document(chunk_id='doc:p1'), 'b')
        self.assertEqual(list(store.items.values()), [first, second])
        for name in ('document_id', 'source', 'source_id', 'source_version', 'section_id',
                     'span_start', 'span_end', 'chunk_sha256', 'document_sha256', 'source_text'):
            self.assertEqual(getattr(first, name), getattr(document(), name))

    def test_distinct_conflicting_evidence_remains_available_to_critic(self):
        store = EvidenceStore(deduplicate=True)
        store.add(document(facts={'result': 'supports'}), 'a')
        store.add(document(content='Alpha contradicts the example.', facts={'result': 'contradicts'}), 'b')
        self.assertEqual(len(store.items), 2)
        self.assertEqual(store.conflicts(), ['result'])

    def test_b4_contract_changes_one_agent_flag_and_no_limits(self):
        enabled = configuration('B4', optimization='source_identity_dedup')
        disabled = configuration('B4off', optimization='source_identity_dedup')
        self.assertEqual({key for key in enabled['flags'] if enabled['flags'][key] != disabled['flags'][key]},
                         {'deduplicate'})
        for key in set(enabled) - {'group', 'flags', 'optimization'}:
            self.assertEqual(enabled[key], disabled[key], key)
        self.assertEqual(enabled['max_input_tokens'], 3072)
        self.assertEqual(enabled['max_output_tokens'], 512)
        self.assertFalse(enabled['flags']['cache'])

    def test_agent_on_off_keeps_tool_and_budget_controls_with_cross_round_merge(self):
        reader = BenchmarkCorpusReader(
            {'doc': [{'unit_id': 'doc:p0', 'locator': 'paragraph0', 'text': 'Alpha supports the example.'}]},
            revision='synthetic-v1', license='synthetic fixture', source_url='https://example.invalid/doc',
        )

        class FixedRetriever:
            backend_name = 'synthetic-fixed-v1'
            def search(self, query):
                return [reader.read('doc:p0')]

        class RecordingProvider(MockProvider):
            def __init__(self):
                super().__init__({
                    'plan': [json.dumps({'queries': ['alpha', 'beta']})],
                    'replan': [json.dumps({'queries': ['gamma']})],
                    'critic': [json.dumps({'sufficient': False, 'reason': 'Synthetic forced bounded replan',
                                           'missing_queries': ['gamma'], 'conflicts': []})],
                })
                self.inputs = []
            def complete(self, operation, payload):
                self.inputs.append((operation, json.loads(json.dumps(payload))))
                return super().complete(operation, payload)

        limits = ResearchLimits(max_tool_calls=18, max_model_calls=8)
        runs = []
        providers = []
        for enabled in (False, True):
            provider = RecordingProvider()
            providers.append(provider)
            run = ResearchEvidenceAgent(
                provider, FixedRetriever(), reader, limits=limits, deduplicate=enabled, cache=False,
            ).run(UserQuery('Synthetic alpha question', 'synthetic-task'))
            runs.append(run)
            self.assertEqual(run['answer']['status'], 'completed')
            self.assertEqual(run['trace']['tool_calls'], 6)
            self.assertEqual(run['trace']['model_calls'], 6)
            self.assertEqual(run['trace']['budget']['limits'], asdict(limits))
            self.assertTrue(run['citation_report']['structural_valid'])
            self.assertFalse(run['citation_report']['semantic_support_complete'])
            self.assertTrue(all(c['evidence_id'] in {e['evidence_id'] for e in run['evidence']}
                                for c in run['answer']['citations']))
        off, on = runs
        self.assertEqual(len(off['evidence']), 3)
        self.assertEqual(len(on['evidence']), 1)
        self.assertEqual(on['evidence'][0]['task_ids'], ['0:0', '0:1', '1:0'])
        self.assertEqual(off['retrieved_chunk_ids'], on['retrieved_chunk_ids'])
        self.assertEqual(off['selected_chunk_ids'], on['selected_chunk_ids'])
        self.assertEqual([op for op, _ in providers[0].inputs], [op for op, _ in providers[1].inputs])
        self.assertEqual([len(p['evidence']) for op, p in providers[0].inputs if op == 'answer'], [2, 3])
        self.assertEqual([len(p['evidence']) for op, p in providers[1].inputs if op == 'answer'], [1, 1])
        self.assertLess(on['trace']['budget']['reserved_tokens'], off['trace']['budget']['reserved_tokens'])
        self.assertIsNone(on['trace']['token_usage'])  # No Mock token count is presented as real usage.


if __name__ == '__main__':
    unittest.main()
