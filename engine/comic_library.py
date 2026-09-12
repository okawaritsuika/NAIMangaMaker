"""Read-only summaries of saved comics; one unreadable project stays one row."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit


def _cover(project_folder, project_id, value):
    if not isinstance(value, str):
        return None
    url = urlsplit(value)
    prefix = '/files/' + project_id + '/'
    decoded = unquote(url.path)
    if url.scheme or url.netloc or url.query or url.fragment or not decoded.startswith(prefix):
        return None
    relative = decoded[len(prefix):]
    path = (project_folder / relative).resolve()
    if (not path.is_relative_to(project_folder.resolve()) or
            path.suffix.lower() not in ('.png', '.webp') or not path.is_file()):
        return None
    return value


def _summary(path):
    project_id = path.parent.name
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('id') != project_id:
        raise ValueError('Invalid project identity')
    panels, pages = value.get('panels'), value.get('pages')
    if (not isinstance(panels, list) or not isinstance(pages, list) or
            any(not isinstance(row, dict) for row in panels + pages)):
        raise ValueError('Invalid panel or page collection')
    title = value.get('title')
    title = title.strip() if isinstance(title, str) else ''
    updated = value.get('updated') if isinstance(value.get('updated'), str) else ''
    cover, rendered = None, 0
    for page in pages:
        image = _cover(path.parent, project_id, page.get('image_url'))
        if image:
            cover = cover or image
            selected = page.get('selected_render_index')
            renders = page.get('renders', [])
            explicitly_unverified = (type(selected) is int and isinstance(renders, list) and
                0 <= selected < len(renders) and isinstance(renders[selected], dict) and
                renders[selected].get('verified') is False)
            if (not page.get('stale') and not explicitly_unverified and
                    page.get('status') not in ('draft', 'rendering', 'failed')):
                rendered += 1
    ended = value.get('ended') is True
    panel_ids = {p.get('id') for p in panels}
    assigned = {panel_id for page in pages for panel_id in page.get('panel_ids', [])}
    status = ('draft' if not pages else 'in_progress' if rendered != len(pages) else
              'complete' if ended and panel_ids <= assigned else 'rendered')
    return dict(id=project_id, title=title or '제목 없는 작품', updated=updated, ended=ended,
                panel_count=len(panels), page_count=len(pages), rendered_count=rendered,
                cover_url=cover, status=status)


def list_library(workbench):
    """Return {projects: [...]} using only saved project JSON and file existence."""
    directory = Path(workbench.directory).resolve()
    projects = []
    for path in directory.glob('p*/project.json'):
        if not re.fullmatch(r'p[0-9a-f]{12}', path.parent.name):
            continue
        try:
            if not path.resolve().is_relative_to(directory):
                raise ValueError('Project path leaves the library')
            projects.append(_summary(path))
        except (OSError, ValueError, TypeError, UnicodeError):
            try:
                updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            except OSError:
                updated = ''
            projects.append(dict(id=path.parent.name, title='읽을 수 없는 작품', updated=updated,
                ended=False, panel_count=0, page_count=0, rendered_count=0, cover_url=None, status='error'))
    projects.sort(key=lambda row: row['updated'], reverse=True)
    return dict(projects=projects)
