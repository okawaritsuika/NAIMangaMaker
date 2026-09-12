"""Offline, bundled Danbooru tag suggestions. No model or network calls."""
import csv
from functools import lru_cache
from pathlib import Path
import sys

ROOT = Path(getattr(sys, '_MEIPASS', str(Path(__file__).parent)))

def normalize(value):
    return ' '.join(value.lower().replace('_', ' ').split())

@lru_cache(maxsize=1)
def catalog():
    records = {}
    for path in sorted((ROOT/'assets/tags').glob('*.csv')):
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                tag = row['tag']
                count = int(row['count']) if row['count'] else 0
                if tag not in records:
                    records[tag] = dict(tag=tag, categories=[], count=count, search=normalize(tag))
                records[tag]['categories'].append(path.stem)
                records[tag]['count'] = max(records[tag]['count'], count)
    return sorted(records.values(), key=lambda row: (-row['count'], row['tag']))

@lru_cache(maxsize=256)
def search(query):
    query = normalize(query)
    if not query or len(query) > 80:
        return []
    exact, prefix, partial = [], [], []
    for row in catalog():
        name = row['search']
        target = exact if name == query else prefix if name.startswith(query) else partial if query in name else None
        if target is not None and len(target) < 20:
            target.append(dict(tag=row['tag'], categories=row['categories']))
        if len(prefix) >= 20 and exact:
            break
    return (exact + prefix + partial)[:20]
