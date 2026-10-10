"""Gold-free corpus/tool boundary using the existing CorpusChunk/BM25/CitationVerifier contracts."""
from hashlib import sha256
from deepresearch.models import UserQuery
from deepresearch.retrieval.corpus import CorpusChunk
from deepresearch.benchmarks.adapters import inference_input


class BenchmarkCorpusReader:
    def __init__(self, units, *, revision, license, source_url, titles=None):
        self.chunks = {}; titles = titles or {}
        for document_id, rows in units.items():
            full_text = '\n\n'.join(row['text'] for row in rows)
            digest = sha256(full_text.encode()).hexdigest(); offset = 0
            for row in rows:
                start = offset; end = start + len(row['text']); offset = end + 2
                chunk = CorpusChunk(document_id, document_id, titles.get(document_id, document_id), source_url,
                                    revision, license, 'locked public dataset release', digest, row['locator'],
                                    row['unit_id'], {'start': start, 'end': end, 'text': row['text']},
                                    row['text'], full_text, 'official paragraph/sentence; no annotations')
                chunk.validate()
                if chunk.chunk_id in self.chunks:
                    raise ValueError('duplicate_benchmark_unit')
                self.chunks[chunk.chunk_id] = chunk

    def read(self, chunk_id):
        return self.chunks[chunk_id].as_document()


def scifact_document_units(sentence_units):
    """R0 document retrieval ranks complete abstracts, not sentences then post-hoc collapsed ranks."""
    return {doc: [{'document_id': doc, 'unit_id': f'scifact:{doc}:abstract', 'locator': 'full_abstract',
                   'text': '\n'.join(row['text'] for row in rows)}] for doc, rows in sentence_units.items()}


def agent_query(task):
    payload = inference_input(task)
    instruction = ('Verify this scientific claim. FinalAnswer.text must be exactly SUPPORT, CONTRADICT, NEI, or ABSTAIN; cite evidence sentences.\n'
                   if task['benchmark_id'] == 'scifact' else
                   'Answer concisely using only the given paper; if not answerable, say Unanswerable. Cite source paragraphs.\n')
    return UserQuery(instruction + payload['text'], payload['task_id'])


def prediction_from_run(task, run, reader):
    answer = run['answer']
    if answer['status'] == 'failed':
        return {'status': 'failed'}
    registry = {row['evidence_id']: row for row in run['evidence']}
    cited = []
    for citation in answer['citations']:
        row = registry.get(citation['evidence_id'])
        if row is None or row['chunk_id'] not in reader.chunks:
            continue
        chunk = reader.chunks[row['chunk_id']]
        if row.get('chunk_sha256') != sha256(chunk.text.encode()).hexdigest():
            continue
        cited.append(chunk)
    if task['benchmark_id'] == 'qasper':
        return {'answer': 'Unanswerable' if answer['status'] == 'insufficient_evidence' else answer['text'],
                'evidence': [chunk.text for chunk in cited]}
    label = answer['text'].strip()
    if label not in {'SUPPORT', 'CONTRADICT', 'NEI', 'ABSTAIN'}:
        return {'label': 'ABSTAIN', 'evidence': {}, 'invalid_output': True}
    evidence = {}
    if label in {'SUPPORT', 'CONTRADICT'}:
        for chunk in cited:
            sentence = int(chunk.chunk_id.rsplit(':s', 1)[1])
            item = evidence.setdefault(chunk.document_id, {'label': label, 'sentences': []})
            if sentence not in item['sentences']:
                item['sentences'].append(sentence)
    return {'label': label, 'evidence': evidence}


def linked_result(task, run_id, run, scores):
    """Offline linkage. Gold stays with scorer; trace contains IDs/hashes, never reference text."""
    if run['answer']['query_id'] != task['task_id'] or run['trace']['query_id'] != task['task_id']:
        raise ValueError('trace_task_id_mismatch')
    return {'run_id': run_id, 'benchmark_id': task['benchmark_id'], 'task_id': task['task_id'],
            'task_hash': task['task_hash'], 'task_family_id': task['task_family_id'],
            'scores': scores, 'run': run}
