from pathlib import Path
SYSTEM = Path(__file__).with_name('default_system.txt').read_text(encoding='utf-8').strip()
def configured_key():
    raise ValueError('설정에서 생성에 사용할 키를 선택해 주세요.')
