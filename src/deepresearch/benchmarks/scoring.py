# Public-distribution provenance/modification notice (2026-10-10).
# This project adapts/reimplements QASPER and SciFact scoring rules for its own
# frozen task schema, failure accounting and retrieval metrics. Original sources:
# - AllenAI QASPER evaluator, Apache-2.0:
#   allenai/qasper-led-baseline@afd0fb96bf78ce8cd8157639c6f6a6995e4f9089,
#   scripts/evaluator.py.
# - AllenAI SciFact evaluator code, Apache-2.0:
#   allenai/scifact@68b98a56d93e0f9da0d2aab4e6c3294699a0f72e,
#   verisci/evaluate/lib/metrics.py.
# QASPER identifies its normalization/token-F1 lineage as SQuAD v1.1.
# SQuAD acknowledgements and the verified SQuAD-explorer MIT notice are retained
# in NOTICE and LICENSES/SQuAD-MIT.txt (Copyright (c) 2020 Pranav Rajpurkar).
# The historical complete SQuAD v1.1 script is not redistributed here.
# Compared with the QASPER source, normalization is compacted, F1 uses an
# algebraically equivalent expression, and evaluation accepts this project's
# schema. SciFact count primitives use dictionaries rather than pandas/classes;
# project-specific claim-label and retrieval metrics are explicitly separate.
# Publication adds only this notice. The body below remains byte-identical to
# frozen research source SHA256:
# ce0aa9fe7792340d19a7dfda3d7a84291a73961374eed468b1adb73c40568407.
# No prompts, metric definitions, scores or historical artifacts are changed.
"""Separate scientific-QA and claim-verification metrics; no evaluated-model self-judge."""
from collections import Counter
import math
import re
import string


def normalized(text):
    text = ''.join(char for char in text.lower() if char not in string.punctuation)
    return ' '.join(re.sub(r'\b(a|an|the)\b', ' ', text).split())


def token_f1(prediction, reference):
    left, right = normalized(prediction).split(), normalized(reference).split()
    common = sum((Counter(left) & Counter(right)).values())
    if not common:
        return 0.0  # Official evaluator: even empty/empty token answer = 0.
    return 2 * common / (len(left) + len(right))


def reference_text(reference):
    kind = reference['answer_type']
    if kind == 'unanswerable':
        return 'Unanswerable'
    if kind == 'extractive':
        return ', '.join(reference['extractive_spans'])
    if kind == 'abstractive':
        return reference['free_form_answer']
    return 'Yes' if reference['yes_no'] else 'No'


def paragraph_f1(predicted, reference):
    if not predicted and not reference:
        return 1.0
    common = len(set(predicted) & set(reference))
    return 2 * common / (len(predicted) + len(reference)) if common else 0.0


def qasper_score(task, prediction, retrieved_units=(), k=5):
    if type(k) is not int or k < 1:
        raise ValueError('invalid_recall_k')
    references = task['gold_reference']; evidence = task['gold_evidence']
    if prediction is None or prediction.get('status') == 'failed':
        return {'answer_f1': 0.0, 'evidence_f1': 0.0, 'evidence_recall_at_k': _qasper_recall(evidence, retrieved_units, k),
                'unanswerable_correct': False, 'missing_or_failed': True}
    answer = prediction['answer']; selected = prediction.get('evidence', [])
    return {'answer_f1': max(token_f1(answer, reference_text(ref)) for ref in references),
            'evidence_f1': max(paragraph_f1(selected, [span['quote'] for span in group]) for group in evidence),
            'evidence_recall_at_k': _qasper_recall(evidence, retrieved_units, k),
            'unanswerable_correct': (normalized(answer) == 'unanswerable') ==
                any(ref['unanswerable'] for ref in references), 'missing_or_failed': False}


def _qasper_recall(references, retrieved, k):
    top = set(list(dict.fromkeys(retrieved))[:k])
    groups = [group for group in references if group]
    return max(sum(bool(top & set(span['unit_ids'])) for span in group) / len(group) for group in groups) if groups else None


def retrieval_metrics(ranked_ids, relevant_ids, k=5):
    if type(k) is not int or k < 1:
        raise ValueError('invalid_recall_k')
    gold = set(relevant_ids); ranked = list(dict.fromkeys(ranked_ids))[:k]
    if not gold:
        return {'recall_at_k': None, 'ndcg_at_k': None, 'reciprocal_rank_at_k': None}
    hits = [int(item in gold) for item in ranked]
    dcg = sum(hit / math.log2(index + 2) for index, hit in enumerate(hits))
    ideal = sum(1 / math.log2(index + 2) for index in range(min(k, len(gold))))
    return {'recall_at_k': sum(hits) / len(gold), 'ndcg_at_k': dcg / ideal,
            'reciprocal_rank_at_k': next((1 / (i + 1) for i, hit in enumerate(hits) if hit), 0.0)}


def scifact_score(task, prediction, retrieved_docs=(), k=5):
    """Official-style abstract/sentence count primitives + separately labeled claim-level adaptation."""
    gold = {}
    for rationale in task['gold_evidence']:
        doc = rationale['document_id']
        gold.setdefault(doc, []).append(rationale)
    failed = prediction is None or prediction.get('status') == 'failed'
    prediction = {} if failed else prediction
    evidence = prediction.get('evidence', {})
    if prediction.get('label') not in {None, 'SUPPORT', 'CONTRADICT', 'NEI', 'ABSTAIN'}:
        raise ValueError('invalid_claim_prediction_label')
    retrieved = correct_abstract = correct_label = retrieved_sentences = correct_sentences = selection_sentences = 0
    for doc, item in evidence.items():
        label = item['label']; sentences = item['sentences']
        if label not in {'SUPPORT', 'CONTRADICT', 'NEI'} or any(type(s) is not int or s < 0 for s in sentences):
            raise ValueError('invalid_scifact_prediction')
        if len(set(sentences)) != len(sentences):
            raise ValueError('duplicate_rationale_sentence')
        if label == 'NEI':
            continue
        retrieved += 1; retrieved_sentences += len(sentences)
        rationales = gold.get(str(doc), [])
        label_matches = bool(rationales) and label == rationales[0]['label']
        sets = [{s['sentence_id'] for s in row['sentences']} for row in rationales]
        complete = set().union(*(group for group in sets if group <= set(sentences))) if sets else set()
        selection_sentences += len(complete)
        correct_sentences += len(complete) if label_matches else 0
        correct_label += int(label_matches)
        correct_abstract += int(label_matches and any(group <= set(sentences[:3]) for group in sets))
    relevant_sentences = sum(len(r['sentences']) for r in task['gold_evidence'])
    counts = {'abstract': {'correct': correct_abstract, 'retrieved': retrieved, 'relevant': len(gold)},
              'abstract_label_only': {'correct': correct_label, 'retrieved': retrieved, 'relevant': len(gold)},
              'sentence_label': {'correct': correct_sentences, 'retrieved': retrieved_sentences, 'relevant': relevant_sentences},
              'sentence_selection': {'correct': selection_sentences, 'retrieved': retrieved_sentences, 'relevant': relevant_sentences}}
    return {'claim_label_correct': not failed and prediction.get('label') == task['gold_reference']['label'],
            'gold_label': task['gold_reference']['label'], 'predicted_label': prediction.get('label', 'FAILED'),
            'retrieval': retrieval_metrics(retrieved_docs, gold, k), 'official_style_counts': counts,
            'abstained': prediction.get('label') == 'ABSTAIN', 'failed': failed}


def precision_recall_f1(counts):
    p = counts['correct'] / counts['retrieved'] if counts['retrieved'] else 0.0
    r = counts['correct'] / counts['relevant'] if counts['relevant'] else 0.0
    return {'precision': p, 'recall': r, 'f1': 2 * p * r / (p + r) if p + r else 0.0}


def label_metrics(rows):
    if not rows:
        return {'n': 0, 'accuracy': None, 'macro_f1': None}
    labels = ('SUPPORT', 'CONTRADICT', 'NEI'); scores = {}
    for label in labels:
        tp = sum(row['gold_label'] == label and row['predicted_label'] == label for row in rows)
        scores[label] = precision_recall_f1({'correct': tp, 'retrieved': sum(row['predicted_label'] == label for row in rows),
                                           'relevant': sum(row['gold_label'] == label for row in rows)})
    return {'n': len(rows), 'accuracy': sum(row['claim_label_correct'] for row in rows) / len(rows),
            'macro_f1': sum(s['f1'] for s in scores.values()) / 3, 'per_class': scores,
            'abstentions': sum(row['abstained'] for row in rows), 'failures': sum(row['failed'] for row in rows)}
