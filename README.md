# persona-corpus-tools

캐릭터 자료(나무위키 분량의 본문 + 분량 있는 대사)를 로컬에서 정리해, 웹 앱에 붙여넣을 수 있는
텍스트로 만드는 **제품 밖 오프라인 CLI**다. 맥에서만 실행한다. 서버 배포·컨테이너·Kubernetes
Job은 없다 — 이 레포가 만드는 것은 사람이 직접 복사해 웹에 붙여넣는 파일뿐이고, 그 뒤의 색인·
검색(RAG)은 `persona-gateway`가 맡는다. 배경은
`docs/handoff-corpus-split-2026-09-17.md`(워크스페이스 루트)를 따른다.

저작권 원문 파일이 서버에 닿는 경로를 구조적으로 없애는 것이 이 분리의 목적이다. 실제 원문과
산출물은 Git 밖 또는 제외된 private 경로에만 두고, 공개 검증에는 합성 입력만 쓴다.

## 현재 범위

| 기능 | 현재 코드 |
| --- | --- |
| 로컬 파일 intake | manifest·SHA-256·보존 조건 확인 |
| Transcript·PDF | 화자·위치를 보존한 canonical 발화 추출 |
| SMI | 준비·수동 화자 검토·확정 구간 내보내기 |
| 위키 HTML | 로컬 원문의 본문·소제목·각주 추출 |
| 품질 검사 | 격리 사유·중복·길이·검토 후보 보고 |
| **붙여넣기 출력** | `persona-wiki-extract`/`persona-smi-export`의 `--format paste` — §4-1 계약(`docs/export-format.md`) |

URL 자동 수집, 음성 인식, 자동 화자 판별, 번역은 제공하지 않는다. crawler·scraper도 작성하지
않는다.

## 로컬 실행

Python 3.11 이상과 uv를 사용한다. PDF 파서는 별도로 Poppler의 `pdftotext`가 필요하다.
아래 명령은 이 저장소 루트에서 합성 fixture를 처리한다.

```sh
uv sync --frozen
OUTPUT_DIR=$(mktemp -d)
uv run persona-ingest \
  --manifest tests/fixtures/synthetic/manifests/sample-series-ep001.yaml \
  --private-root tests/fixtures/synthetic \
  --personas configs/personas.yaml \
  --out "$OUTPUT_DIR"
```

출력은 지정한 디렉터리의 `parsed/`, `quarantine/`, `reports/`, `run_report.json`에 저장한다.
입력 manifest·해시 검증 실패를 정상 처리 결과로 넘기지 않는다.
기준 데이터는 JSONL이며 CSV는 미리보기용이다. Parquet은 선택 의존성으로 제공한다.

## 웹에 붙여넣을 출력 만들기

검토가 끝난 자료를 §4-1 계약(`docs/export-format.md`)에 맞는 텍스트로 뽑는다. `--out`은
`--format paste`일 때 디렉터리가 아니라 **파일 경로**다.

```sh
uv run persona-wiki-extract --input-root <acquisition 폴더> --out paste.md --format paste
uv run persona-smi-export --prepared <prepared> --annotations <annotations> \
  --personas configs/personas.yaml --out speech.txt --format paste
```

`paste.md`는 그대로, `speech.txt`는 대사 예시 칸에 웹 앱에 복사해 붙여넣는다. 두 명령 모두
`--format jsonl`(기본값)로 기존 비공개 검토용 구조화 출력도 그대로 낸다.

## 검증

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

로컬 파서 테스트만 검증하며, 이 레포는 어떤 서버·DB·Kubernetes 대상과도 통합 검증하지 않는다
(그럴 대상이 없다). 화자·locator 보존, 같은 입력의 재현성, 오류 격리와 원문 비노출을 확인한다.
네트워크 파일시스템에서 가상환경·캐시 오류가 발생하면 캐시를 로컬 디스크로 분리한다.
