# NAIMangaMaker

NovelAI로 이야기와 만화 페이지를 만들고 편집하는 Windows용 로컬 작업실입니다. 개인 NovelAI API 키가 필요합니다.

## 시작

1. [릴리스](https://github.com/okawaritsuika/NAIMangaMaker/releases/latest)에서 `NAIMangaMaker.exe`를 내려받아 쓰기 가능한 폴더에 저장합니다.
2. EXE를 실행하고 **서버 켜기**를 누릅니다. **자동으로 웹페이지 열기**를 켜면 준비 후 작업실이 열립니다.
3. 작업실 오른쪽 **설정**에서 자신의 API 키를 등록합니다.
4. 리모콘을 닫으면 해당 리모콘이 시작한 서버도 종료됩니다. 생성 중에는 완료 후 닫으세요.

작업실 주소는 `http://127.0.0.1:8795/`입니다. 포트를 다른 프로그램이 점유하면 종료 여부를 물어봅니다.

## 저장 및 업데이트

저장된 설정, 키, 캐릭터, 작품, 이미지 및 이력은 `%LOCALAPPDATA%\NAIMangaMaker`에 보관합니다. 서버 종료나 EXE 교체로 지워지지 않습니다. 키는 현재 Windows 사용자 계정의 DPAPI로 암호화하므로 다른 컴퓨터에서는 다시 등록해야 합니다. 아직 저장하지 않은 편집 입력은 브라우저 보관 방식이며 브라우저 데이터 삭제 시 사라질 수 있습니다.

리모콘의 **업데이트 확인**으로 이 저장소의 새 정식 릴리스를 확인합니다. **업데이트 설치**를 승인하면 다운로드 크기와 SHA-256을 검증하고 서버를 종료한 뒤 EXE를 교체합니다. 이전 EXE는 같은 폴더의 `NAIMangaMaker.previous.exe`에 남깁니다. 실패 시 원상복구를 시도합니다. 개인 데이터 폴더는 업데이트 대상이 아닙니다. 백업하려면 서버를 끈 뒤 위 데이터 폴더를 별도로 복사하세요.

배포본에는 태그 사전, 프로그램 기본 프롬프트와 일반 생성 수치만 들어갑니다. 제작자의 API 키, 개인 캐릭터, 그림체 설정, 작품, 로그는 포함하지 않습니다.

## 기능

- 이야기 시작, 앞뒤 연결·동작·강조 확장 및 자동 완성
- 페이지 구성, 이미지 생성·재생성·편집, 캐릭터 위치 조절
- 이미지 기본값, 캐릭터, 단계별 프롬프트 관리
- 태그 자동완성, 만화 일람 및 큰 그림 보기
- 여러 API 키의 이야기·이미지 역할 지정 및 사용량 표시

## 소스 실행 / 빌드

Windows 10/11 x64와 Tcl/Tk가 포함된 Python 3.12를 사용합니다.

```powershell
python -m pip install -r requirements.txt
python remote.py
# EXE 빌드
./build.ps1
```

실행 파일은 `dist/NAIMangaMaker.exe`에 만들어집니다. `python -m unittest test_updater`로 업데이트 검증·실패 복구 테스트를 실행할 수 있습니다.

다음 버전을 배포할 때는 `app_version.py`의 버전을 변경하고 `v숫자.숫자.숫자` 태그의 GitHub 릴리스에 동일한 이름의 EXE를 첨부하세요. GitHub가 제공하는 asset SHA-256 값으로 검증합니다.

## 포함 자료

태그: [Jio7/danbooru-tags-classified](https://huggingface.co/datasets/Jio7/danbooru-tags-classified). 출처와 원본 데이터 카드는 `assets/tags`에 포함합니다. 아이콘은 프로젝트에서 도형으로 제작했습니다. 실행 환경의 라이선스는 `licenses`에 포함합니다. NovelAI 공식 제품은 아닙니다.
