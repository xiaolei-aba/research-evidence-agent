"""Parse official original schemas. Gold is retained only in offline task records."""
from collections import Counter
from hashlib import sha256
import json


def canonical_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def seal_task(task):
    task['task_hash'] = canonical_hash(task)
    return task


def answer_type(answer):
    if answer['unanswerable']:
        return 'unanswerable'
    if answer.get('extractive_spans'):
        return 'extractive'
    if answer.get('free_form_answer', '').strip():
        return 'abstractive'
    if answer.get('yes_no') is not None:
        if type(answer['yes_no']) is not bool:
            raise ValueError('yes_no_must_be_bool_or_null')
        return 'boolean'
    raise ValueError('empty_answerable_reference')


def qasper_paper(paper_id, paper):
    """Full title/abstract/section paragraphs/captions; exact paragraph IDs, not reference highlights."""
    items = [('title', paper['title']), ('abstract', paper['abstract'])]
    for section_index, section in enumerate(paper['full_text']):
        for paragraph_index, text in enumerate(section['paragraphs']):
            items.append((f's{section_index}:p{paragraph_index}', text))
    for index, figure in enumerate(paper.get('figures_and_tables', [])):
        items.append((f'caption{index}', figure['caption']))
    return [{'document_id': paper_id, 'unit_id': f'qasper:{paper_id}:{key}', 'locator': key, 'text': text}
            for key, text in items if isinstance(text, str) and text.strip()]


def parse_qasper(data, split, revision):
    tasks = []; units = {}; errors = []; counts = Counter()
    for paper_id, paper in sorted(data.items()):
        paragraphs = qasper_paper(paper_id, paper); units[paper_id] = paragraphs
        for question in paper['qas']:
            try:
                references = []; evidence_sets = []; types = []
                for annotation in question['answers']:
                    answer = annotation['answer']; kind = answer_type(answer); types.append(kind)
                    matches = []
                    for text in ([] if kind == 'unanswerable' else answer.get('evidence', [])):
                        located = [row['unit_id'] for row in paragraphs if row['text'] == text]
                        if not located:
                            # Upstream figure/table evidence can be unavailable in text-only input.
                            raise ValueError('evidence_not_exactly_located')
                        matches.append({'quote': text, 'unit_ids': located})
                    if kind != 'unanswerable' and not matches:
                        raise ValueError('answerable_reference_without_evidence')
                    references.append({'annotation_id': annotation.get('annotation_id'), 'answer_type': kind,
                                       'unanswerable': answer['unanswerable'], 'yes_no': answer.get('yes_no'),
                                       'extractive_spans': answer.get('extractive_spans', []),
                                       'free_form_answer': answer.get('free_form_answer', '')})
                    evidence_sets.append(matches)
                if not references:
                    raise ValueError('no_gold_references')
                category = types[0] if len(set(types)) == 1 else 'mixed_reference_types'
                task = {'benchmark_id': 'qasper', 'split': split, 'task_id': 'qasper:' + question['question_id'],
                        'task_family_id': 'paper:' + paper_id, 'source_doc_ids': [paper_id],
                        'question_or_claim': question['question'], 'gold_reference': references,
                        'gold_evidence': evidence_sets, 'answer_type': category, 'label_type': 'official_human_annotation',
                        'difficulty_or_category': category, 'license': 'CC-BY-4.0 dataset; underlying paper rights retained',
                        'dataset_revision': revision, 'review_status': 'upstream_human_gold; mechanically_source_mapped'}
                if not task['question_or_claim'].strip():
                    raise ValueError('empty_question')
                tasks.append(seal_task(task)); counts[category] += 1
            except (KeyError, TypeError, ValueError) as exc:
                errors.append({'paper_id': paper_id, 'question_id': question.get('question_id'), 'reason': str(exc)})
    if len({task['task_id'] for task in tasks}) != len(tasks):
        raise ValueError('duplicate_qasper_question_id')
    return tasks, units, {'papers': len(data), 'raw_questions': sum(len(p['qas']) for p in data.values()),
                          'usable_questions': len(tasks), 'categories': dict(counts), 'excluded': errors}


def parse_scifact(corpus, claims, split, revision, gold_available=True):
    docs = {str(row['doc_id']): row for row in corpus}
    if len(docs) != len(corpus):
        raise ValueError('duplicate_scifact_document_id')
    tasks = []; errors = []; counts = Counter()
    for claim in claims:
        try:
            rationales = []; labels = set()
            if not gold_available:
                raise ValueError('official_test_gold_unavailable')
            for document_id, sets in claim['evidence'].items():
                doc = docs[str(document_id)]
                for evidence in sets:
                    label = evidence['label']
                    if label not in {'SUPPORT', 'CONTRADICT'} or not evidence['sentences']:
                        raise ValueError('invalid_rationale_or_label')
                    labels.add(label); located = []
                    for index in evidence['sentences']:
                        if type(index) is not int or not 0 <= index < len(doc['abstract']):
                            raise ValueError('sentence_index_out_of_range')
                        located.append({'sentence_id': index, 'unit_id': f'scifact:{document_id}:s{index}',
                                        'quote': doc['abstract'][index]})
                    rationales.append({'document_id': str(document_id), 'label': label, 'sentences': located})
            label = next(iter(labels)) if len(labels) == 1 else 'NEI' if not labels else 'MIXED'
            family_docs = sorted(set(map(str, claim['cited_doc_ids'])) | {r['document_id'] for r in rationales})
            if any(document_id not in docs for document_id in family_docs):
                raise ValueError('cited_document_missing_from_corpus')
            task = {'benchmark_id': 'scifact', 'split': split, 'task_id': 'scifact:' + str(claim['id']),
                    'task_family_id': 'cited:' + '|'.join(family_docs), 'source_doc_ids': family_docs,
                    'question_or_claim': claim['claim'], 'gold_reference': {'label': label},
                    'gold_evidence': rationales, 'answer_type': 'claim_label', 'label_type': 'official_human_annotation',
                    'difficulty_or_category': label, 'license': 'claims CC-BY-4.0; abstracts ODC-BY-1.0',
                    'dataset_revision': revision, 'review_status': 'upstream_human_gold; mechanically_source_mapped'}
            tasks.append(seal_task(task)); counts[label] += 1
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({'claim_id': claim.get('id'), 'reason': str(exc)})
    if len({task['task_id'] for task in tasks}) != len(tasks):
        raise ValueError('duplicate_scifact_claim_id')
    return tasks, {'raw_claims': len(claims), 'usable_claims': len(tasks), 'labels': dict(counts), 'excluded': errors}


def inference_input(task):
    """Only genuine task text and QASPER's GIVEN paper scope. SciFact cited IDs are never given."""
    result = {'task_id': task['task_id'], 'text': task['question_or_claim']}
    if task['benchmark_id'] == 'qasper':
        result['paper_id'] = task['source_doc_ids'][0]
    return result


def fixed_sample(tasks, count, seed=20261009, paper_cap=None, excluded_docs=()):
    """Round-robin strata, SHA(seed/task_id) ordering, source-cap rules; no scores inspected."""
    buckets = {}
    for task in tasks:
        if set(task['source_doc_ids']) & set(excluded_docs) or task['difficulty_or_category'] == 'MIXED':
            continue
        buckets.setdefault(task['difficulty_or_category'], []).append(task)
    for rows in buckets.values():
        rows.sort(key=lambda row: sha256(f'{seed}:{row["task_id"]}'.encode()).hexdigest())
    chosen = []; families = Counter()
    while len(chosen) < count and any(buckets.values()):
        for key in sorted(buckets):
            while buckets[key]:
                task = buckets[key].pop(0)
                if paper_cap is not None and families[task['task_family_id']] >= paper_cap:
                    continue
                chosen.append(task); families[task['task_family_id']] += 1
                break
            if len(chosen) == count:
                break
    return chosen
