#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path
from typing import Any

import nltk
import numpy as np
from bertopic import BERTopic
from hdbscan import HDBSCAN
from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import CountVectorizer
from umap import UMAP

ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT.parent / 'first_change_commits' / 'first_change_commits.jsonl'
DEFAULT_OUTPUT = ROOT
DEFAULT_MODEL = 'BAAI/bge-base-en-v1.5'
DEFAULT_RANDOM_SEED = 42


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='BERTopic API first-change commit-message topic pipeline.')
    parser.add_argument('--input-jsonl', default=str(DEFAULT_INPUT))
    parser.add_argument('--output-root', default=str(DEFAULT_OUTPUT))
    parser.add_argument('--embedding-model', default=DEFAULT_MODEL)
    parser.add_argument('--random-seed', type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument('--top-n-words', type=int, default=10)
    parser.add_argument('--max-docs', type=int, default=None)
    parser.add_argument('--overwrite', action='store_true')
    return parser.parse_args()


def reset_outputs(output_root: Path) -> None:
    for rel in [
        'prepared_messages.jsonl',
        'topic_assignments.jsonl',
        'topic_info.json',
        'summary.json',
        'topic_examples.json',
    ]:
        p = output_root / rel
        if p.exists():
            p.unlink()
    embeddings = output_root / 'embeddings.npy'
    if embeddings.exists():
        embeddings.unlink()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open('r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def ensure_nltk() -> None:
    for pkg in ['wordnet', 'omw-1.4', 'punkt']:
        try:
            nltk.data.find(pkg if '/' in pkg else f'corpora/{pkg}')
        except LookupError:
            try:
                nltk.download(pkg, quiet=True)
            except Exception:
                pass


def preprocess_message(message: str, lemmatizer: Any) -> str:
    text = message.lower().strip()
    text = re.sub(r'[_\-/]+', ' ', text)
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    tokens = re.findall(r'[a-z0-9]+', text)
    lemmas = [lemmatizer.lemmatize(tok) for tok in tokens if tok.strip()]
    text = ' '.join(lemmas)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')


def main() -> None:
    args = parse_args()
    input_jsonl = Path(args.input_jsonl).resolve()
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if args.overwrite:
        reset_outputs(output_root)

    random.seed(args.random_seed)
    np.random.seed(args.random_seed)

    rows = read_jsonl(input_jsonl)
    if args.max_docs is not None:
        rows = rows[: args.max_docs]
    ensure_nltk()
    lemmatizer = nltk.stem.WordNetLemmatizer()

    prepared_rows: list[dict[str, Any]] = []
    docs: list[str] = []
    raw_docs: list[str] = []
    for idx, row in enumerate(rows):
        raw = str(row.get('first_change_commit_message') or '').strip()
        cleaned = preprocess_message(raw, lemmatizer)
        if len(cleaned) < 4:
            continue
        prepared = {
            'doc_index': len(prepared_rows),
            'source_index': idx,
            'match_id': row.get('match_id'),
            'repo_full_name': row.get('repo_full_name'),
            'language': row.get('language'),
            'first_change_commit_oid': row.get('first_change_commit_oid'),
            'first_change_commit_url': row.get('first_change_commit_url'),
            'first_change_date': row.get('first_change_date'),
            'raw_message': raw,
            'cleaned_message': cleaned,
        }
        prepared_rows.append(prepared)
        docs.append(cleaned)
        raw_docs.append(raw)

    write_jsonl(output_root / 'prepared_messages.jsonl', prepared_rows)
    print(f'[prepared] input_rows={len(rows)} prepared_rows={len(prepared_rows)}')

    embeddings_path = output_root / 'embeddings.npy'
    if embeddings_path.exists():
        embeddings = np.load(embeddings_path)
        print(f'[cache] loaded embeddings {embeddings.shape}')
    else:
        embedding_model = SentenceTransformer(args.embedding_model)
        embeddings = embedding_model.encode(docs, batch_size=32, show_progress_bar=True, normalize_embeddings=True)
        embeddings = np.asarray(embeddings)
        np.save(embeddings_path, embeddings)
        print(f'[embed] saved embeddings {embeddings.shape}')

    umap_model = UMAP(
        n_neighbors=10,
        n_components=10,
        min_dist=0.2,
        metric='cosine',
        random_state=args.random_seed,
    )
    hdbscan_model = HDBSCAN(
        min_cluster_size=10,
        min_samples=5,
        metric='euclidean',
        cluster_selection_method='eom',
        prediction_data=False,
    )
    vectorizer_model = CountVectorizer(stop_words='english', ngram_range=(1, 3))
    topic_model = BERTopic(
        embedding_model=None,
        umap_model=umap_model,
        hdbscan_model=hdbscan_model,
        vectorizer_model=vectorizer_model,
        top_n_words=args.top_n_words,
        calculate_probabilities=False,
        verbose=True,
    )
    topics, _ = topic_model.fit_transform(docs, embeddings)

    doc_info = topic_model.get_document_info(docs)
    assignments: list[dict[str, Any]] = []
    for prepared, topic, _, row in zip(prepared_rows, topics, docs, doc_info.to_dict(orient='records')):
        rec = dict(prepared)
        rec['topic_id'] = int(topic)
        rec['is_noise'] = int(topic) == -1
        rec['topic_name'] = row.get('Name')
        rec['representative'] = row.get('Representative_document')
        assignments.append(rec)
    write_jsonl(output_root / 'topic_assignments.jsonl', assignments)

    topic_info = topic_model.get_topic_info().to_dict(orient='records')
    (output_root / 'topic_info.json').write_text(json.dumps(topic_info, indent=2, ensure_ascii=False), encoding='utf-8')

    topic_examples: dict[str, list[dict[str, Any]]] = {}
    by_topic: dict[int, list[dict[str, Any]]] = {}
    for rec in assignments:
        by_topic.setdefault(rec['topic_id'], []).append(rec)
    for topic_id, rows_for_topic in by_topic.items():
        if topic_id == -1:
            continue
        topic_examples[str(topic_id)] = [
            {
                'match_id': r['match_id'],
                'repo_full_name': r['repo_full_name'],
                'raw_message': r['raw_message'],
                'first_change_commit_oid': r['first_change_commit_oid'],
            }
            for r in rows_for_topic[:10]
        ]
    (output_root / 'topic_examples.json').write_text(json.dumps(topic_examples, indent=2, ensure_ascii=False), encoding='utf-8')

    topic_info_non_noise = [row for row in topic_info if int(row.get('Topic', -1)) != -1]
    noise_count = next((int(row.get('Count', 0)) for row in topic_info if int(row.get('Topic', -999)) == -1), 0)
    summary = {
        'input_rows': len(rows),
        'prepared_rows': len(prepared_rows),
        'embedding_model': args.embedding_model,
        'umap_params': {
            'n_neighbors': 10,
            'n_components': 10,
            'min_dist': 0.2,
            'metric': 'cosine',
        },
        'hdbscan_params': {
            'min_cluster_size': 10,
            'min_samples': 5,
            'metric': 'euclidean',
            'cluster_selection_method': 'eom',
        },
        'topic_count_excluding_noise': len(topic_info_non_noise),
        'noise_document_count': noise_count,
        'non_noise_document_count': len(prepared_rows) - noise_count,
    }
    (output_root / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"[done] prepared={len(prepared_rows)} topics={summary['topic_count_excluding_noise']} noise={summary['noise_document_count']}")


if __name__ == '__main__':
    main()
