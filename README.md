# persona-ingestion

사용자가 로컬에서 적법하게 확보한 structured transcript/script 파일을
화자 기준 canonical utterance corpus로 변환하는 batch pipeline.

기획 문서: [`docs/repository-plans/persona-ingestion.md`](../docs/repository-plans/persona-ingestion.md)

## 하지 않는 것

- URL crawler, transcript scraper/indexer, subtitle downloader
- ASR, diarization, timestamp–subtitle alignment, 자동 번역
- online chat API, GPU model serving
- 실제 원문의 저장소 커밋 또는 공개 산출물 반출

입력은 **사용자가 파일 시스템에 직접 둔 파일**뿐이다. 네트워크에서 원문을 가져오는 코드는 이 레포에 존재하지 않는다.

## 디렉토리

```
src/persona_ingestion/
├── intake/      # 파일 등록, SHA-256, source manifest 로드/검증
├── adapters/    # 입력 포맷별 parser (transcript_txt, script_pdf)
├── canonical/   # utterance schema, 정규화, alias 매핑, 중복 표시
├── quality/     # 품질 게이트, leakage 검사, 표본 추출
├── reporting/   # parse report, quarantine 기록
└── cli/         # 실행 진입점
configs/         # persona alias / parser profile 설정
tests/
├── unit/
├── integration/
└── fixtures/synthetic/
    ├── transcript_txt/   # 합성 시간+화자 대본
    ├── script_pdf/       # 합성 촬영 대본 PDF
    └── expected/         # 기대 canonical 출력
image/           # 컨테이너 빌드 (private PV에서 Job으로 실행)
docs/
```

`private-data/`는 이 레포 안에 만들지 않는다. Git 밖의 별도 경로 또는 Kubernetes PV에 둔다.

```
<private root>/
├── raw/            # 사용자가 넣은 원본 TXT/PDF
├── manifests/      # source_manifest.yaml
├── parsed/         # canonical JSONL/Parquet
└── quarantine/     # 파싱 실패 라인 + 사유
```

## 툴링

- Python 패키지/가상환경: **uv** (Python >= 3.11)
- 빌드 설정: `pyproject.toml` (src layout)
- lint/format: **ruff**, 타입: **mypy** (strict), 테스트: **pytest**

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

작업 디렉토리가 네트워크/FUSE 마운트 위에 있으면 venv와 캐시를 마운트 밖에 두어야 한다
(mypy의 sqlite 캐시가 마운트에서 `disk I/O error`로 실패한다):

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/venvs/persona-ingestion"
export UV_CACHE_DIR="$HOME/.cache/uv"
export MYPY_CACHE_DIR="$HOME/.cache/mypy"
```

## 실행 (Loop 1)

```bash
uv run persona-ingest \
  --input   tests/fixtures/synthetic/transcript_txt/sample_series_ep001.txt \
  --personas configs/personas.yaml \
  --character character_a \
  --source-id sample-series-ep001 --series sample-series --episode 001 \
  --out /path/outside/git
```

출력은 `--out` 아래 `parsed/`, `quarantine/`, `reports/`에 각각 쓰인다.
Loop 1은 source 식별값을 CLI 인자로 받고, Loop 2에서 `source_manifest.yaml`로 대체한다.
adapter 계약(`SourceMeta`)은 그대로 유지된다.

## 데이터 포맷

기준 데이터는 **JSONL** (대용량 시 Parquet 병행). CSV는 사람이 훑어보기 위한
파생 preview일 뿐이며 downstream이 CSV를 읽지 않는다.

레코드 스키마와 `source_manifest.yaml` 필드는 기획 문서를 따른다.
`series` / `episode` 값은 파일명 추측이 아니라 **manifest에 명시된 값**을 사용한다.

## 파서 규칙 (transcript_txt)

블록은 `[MM:SS] Speaker` 헤더에서 시작해 다음 헤더 또는 파일 끝까지다.
블록 안의 빈 줄은 구분자일 뿐 종료가 아니다.

| 상황 | 처리 |
| --- | --- |
| alias 일치 (`Char A`, 대소문자 무관) | 대상 발화로 추출 |
| `Character A (whispering)` | 뒤쪽 전달 방식 주석만 떼고 alias 매칭, `speaker_raw`는 원문 보존 |
| 다른 persona / non-dialogue 화자(`Sign`) / 알 수 없는 화자 | 제외하고 report에 계수 (quarantine 아님) |
| `[0:33 Character A` 같은 깨진 헤더 | `malformed_header`로 quarantine |
| 깨진 헤더 뒤의 본문 줄 | `orphaned_body_after_malformed_header`로 quarantine — **앞 화자에 붙이지 않는다** |
| 첫 헤더 이전의 텍스트 | `text_before_first_header` |
| 본문 없는 헤더 | `empty_body` |
| `[NOTICE] ...` 처럼 대괄호로 시작하지만 타임스탬프가 아닌 줄 | 본문으로 취급 |

헤더 후보 판정은 `[` 뒤에 숫자가 오는 경우로 한정한다. 화면 표기(`[Sign]`)를 헤더로
오인하지 않기 위해서다.

`utterance_id`는 `{source_id}-t{초:05d}-{블록순번:04d}`이며, 블록 순번은 제외된 블록까지
포함해 파일 전체에서 매긴다. 대상 persona를 바꿔도 같은 블록은 같은 순번을 유지한다.

## 품질 게이트

- 대상 화자 외 발화 leakage 0건
- locator(timestamp 또는 page) 누락 0건
- 파싱 실패는 버리지 않고 사유와 함께 quarantine에 기록
- source별 표본 20개를 원문과 대조
- 같은 입력을 다시 실행하면 동일한 output hash
- 공개 test/benchmark는 합성 fixture만 사용

## 구현 루프

1. **Loop 1** — 합성 transcript parser: multiline merge, alias 매핑, 비대상 화자 제외, malformed line quarantine
2. **Loop 2** — canonicalization/report: JSONL/Parquet, manifest, source hash, parse stats, 재현성
3. **Loop 3** — script PDF adapter: layout-preserving 추출 + speaker heading state machine
4. **Loop 4** — private Kubernetes Job: raw PV read-only mount, 결과 PV에만 쓰기
5. **Loop 5** — indexing handoff: token-aware chunk → embedding → Qdrant upsert

**현재 상태: Loop 1 완료** (transcript_txt adapter, 50 tests, ruff/mypy strict 통과).
다음 작업은 Loop 2 — `source_manifest.yaml` 기반 intake와 재현성 게이트.
