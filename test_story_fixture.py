"""Offline SFW model fixture; real writer/translation/director validation remains active.

UI integration: RemoteApp(DemoWorkbench(temporary_data_directory)). No credential
provider, network transport, or image generation is invoked by this fixture.
"""
import copy
import json
from pathlib import Path

try:
    from .engine.iterative_comic import Workbench, read, save
    from .engine.iterative_recovery import current_session
except ImportError:
    from engine.iterative_comic import Workbench, read, save
    from engine.iterative_recovery import current_session

APPEARANCE = ['adult gardener', 'short brown hair', 'green shirt', 'blue trousers', 'brown shoes']
CAST = {'actor1': dict(name='Alex', personality='Patient and kind', appearance=APPEARANCE)}
BEATS = [
    ('Alex steadies a small flowerpot on the garden table.', 'steadying a flowerpot'),
    ('Alex places a seed into the soil with a careful fingertip.', 'leaning over the flowerpot'),
    ('Alex smiles beside the planted flowerpot in the garden.', 'standing beside the flowerpot'),
    ('Alex points toward the flowerpot while admiring the garden.', 'pointing toward the flowerpot'),
    ('Alex rests both hands on the table beside the flowerpot.', 'resting hands on the table'),
]


def final_json(task):
    decoder = json.JSONDecoder()
    for index, character in enumerate(task):
        if character != '{':
            continue
        try:
            value, end = decoder.raw_decode(task[index:])
        except ValueError:
            continue
        if not task[index + end:].strip():
            return value
    raise AssertionError('No final JSON context in fixture task')


class DemoWorkbench(Workbench):
    def __init__(self, directory):
        super().__init__(directory, key_provider=self.no_key)
        self.image_key_provider = self.no_key
        self.demo_calls = []

    @staticmethod
    def no_key():
        raise AssertionError('Offline fixture must never request credentials')

    def call(self, folder, name, task, **kwargs):
        folder = Path(folder)
        target = folder / name
        session = current_session(self)
        if session:
            session.stage_started(name, target)
        cached = target / 'fixture_result.json'
        if cached.exists():
            result = read(cached)
        else:
            self.demo_calls.append(dict(stage=name, task=task, model=kwargs.get('model', 'glm-4-6')))
            result = self.reply(folder, name, task)
            save(cached, result)
            save(target / 'result.json', dict(http_status=200, finish_reason='stop', offline_fixture=True))
            (target / 'story.txt').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        if session:
            session.stage_completed(name, target)
        return copy.deepcopy(result)

    def reply(self, folder, name, task):
        if name == 'input_translation':
            result = final_json(task)
            def english(value):
                if isinstance(value, str):
                    return value if value.isascii() else 'Tend a small garden'
                if isinstance(value, list): return [english(item) for item in value]
                if isinstance(value, dict): return {key: english(item) for key, item in value.items()}
                return value
            return english(result)
        if name == 'cast_plan':
            return dict(characters=[dict(id='actor1', name='Alex', personality='Patient and kind', appearance=', '.join(APPEARANCE))])
        if name == 'story':
            data = read(folder / 'input_en.json')
            previous = read(folder / 'before.json') if (folder / 'before.json').exists() else {}
            panels = []
            for index in range(data['count']):
                number = len(previous.get('panels', [])) + index
                description, pose = BEATS[number % len(BEATS)]
                panels.append(dict(actor='actor1', description=description, background='A sunlit garden table',
                    dialogues=[] if data.get('dialogue') == 'none' else ['The garden looks lovely.'],
                    camera=dict(subjects=['actor1'], focus='Alex and the flowerpot',
                                framing='medium shot' if number % 2 == 0 else 'close-up', angle='side view'),
                    visual_states={'actor1': dict(appearance=APPEARANCE, held_objects=[], pose=pose)}, importance='normal'))
            return dict(title='A Small Garden', setting='A sunlit garden', cast=copy.deepcopy(CAST),
                        panels=panels, ended=False, conflict=None)
        if name == 'state_resolution':
            context = final_json(task)
            return dict(panels=[dict(index=row['index'], visual_states=row['visual_states']) for row in context['panels']])
        if name == 'output_translation':
            context = final_json(task)
            return dict(title='작은 정원', place_ko='햇빛이 드는 정원',
                characters_ko=[dict(id='actor1', name='알렉스', personality='차분하고 친절함', appearance='초록색 셔츠와 파란 바지를 입은 성인 정원사')],
                panels=[dict(id=row['id'], description_ko='알렉스가 정원의 작은 화분을 돌본다.',
                    background_ko='햇빛이 드는 정원의 탁자', state_ko='초록색 셔츠와 파란 바지',
                    camera_ko=dict(focus='알렉스와 화분', framing='중간 거리', angle='측면'),
                    dialogues_ko=['정원이 참 예쁘네.' for line in row.get('dialogues', [])]) for row in context['panels']])
        if name == 'director':
            context = final_json(task)
            stop = context['accepted_panel_count'] >= 3
            return dict(action='stop' if stop else 'finish', count=min(2, context['current_page_slots']),
                intent='story', dialogue='with', layout='auto',
                instruction='' if stop else 'Show Alex smiling beside the planted flowerpot.',
                reason_en='The accepted final image resolves the gardening task.' if stop else 'The planted flowerpot needs a final visible reaction.',
                reason_ko='마지막 장면에서 정원 가꾸기를 마쳤어요.' if stop else '심은 화분을 바라보는 마지막 반응이 필요해요.')
        raise AssertionError('Unexpected offline fixture stage: ' + name)
