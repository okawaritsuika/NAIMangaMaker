---
license: mit
task_categories:
- text-classification
language:
- en
tags:
- danbooru
- anime
- tags
size_categories:
- 100K<n<1M
---

# danbooru-tags-classified

Danbooru tags split into categories, one CSV per category. Each CSV is `tag,count`
sorted by count descending, where `count` is the tag's post frequency on Danbooru.

| file | tags | contents |
|---|---|---|
| `artist.csv` | 83355 | artist names |
| `character.csv` | 57653 | character names |
| `series.csv` | 12405 | copyright / series names |
| `other.csv` | 15942 | not yet assigned to a category |
| `attire.csv` | 9646 | clothing and worn items |
| `object.csv` | 4227 | objects |
| `feature.csv` | 2930 | body and appearance features |
| `action.csv` | 2500 | poses and actions |
| `setting.csv` | 893 | scene and background |
| `meme.csv` | 566 | memes |
| `meta.csv` | 520 | image metadata tags |
| `expression.csv` | 285 | facial expressions |
| `style.csv` | 209 | art styles |
| `count.csv` | 31 | subject count tags (`1girl`, `2boys`, …) |

## requiring.txt

JSON Lines. Tags that only make sense when some garment is already worn, e.g.
`skirt_lift` needs a `skirt`.

```json
{"tag": "skirt_lift", "category": "action", "required_attires": ["skirt"]}
```

393 entries covering 109 distinct garments.

## Notes

Categories were assigned by an LLM pass over the tag list and are not hand-verified;
expect some noise, especially in `other.csv`.
