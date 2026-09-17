# persona-ingestion

캐릭터 자료를 검증·파싱·정규화하는 Python 배치 도구다.
로컬 파일 처리와 DB 입력을 읽는 Job worker를 구분하며, 온라인 API나 GPU 모델 서빙은 담당하지 않는다.

## 현재 범위

| 기능             | 현재 코드                                      |
| ---------------- | ---------------------------------------------- |
| 로컬 파일 intake | manifest·SHA-256·보존 조건 확인                |
| Transcript·PDF   | 화자·위치를 보존한 canonical 발화 추출         |
| SMI              | 준비·수동 화자 검토·확정 구간 내보내기         |
| 위키 HTML        | 로컬 원문의 본문·소제목·출처 추출              |
| 품질 검사        | 격리 사유·중복·길이·검토 후보 보고             |
| Job worker       | DB의 작업 시도 입력을 읽고 최소 정제 결과 기록 |

청킹·임베딩·Qdrant 인덱싱과 웹에서 시작하는 전체 Job 연동은 미완료다.
Job worker 코드가 있다는 사실을 현재 Gateway 스키마와 호환되는 운영 기능으로 보지 않는다.
자료 처리 성공도 캐릭터 활성화나 채팅 준비 완료를 의미하지 않는다.

URL 자동 수집, 음성 인식, 자동 화자 판별, 번역은 제공하지 않는다.
실제 원문과 산출물은 Git 밖 또는 제외된 private 경로에 보관하며 공개 검증에는 합성 입력만 사용한다.

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

## 검증

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

로컬 파서 테스트와 실제 DB·Kubernetes Job 연동은 서로 다른 검증이다.
화자·locator 보존, 같은 입력의 재현성, 오류 격리와 원문 비노출을 확인한다.
네트워크 파일시스템에서 가상환경·캐시 오류가 발생하면 캐시를 로컬 디스크로 분리한다.

## Job worker 실행 조건

`persona-ingest-job`는 `DATABASE_URL`, `PERSONA_JOB_ID`, `PERSONA_ATTEMPT_ID`를 요구한다.
연결 대상에 해당 Job 스키마·입력·권한이 준비돼 있어야 하며 운영 DB에 임의 실행하지 않는다.

worker는 입력을 읽고 처리 결과를 기록한다. Kubernetes Job 생성, 전역 실행 슬롯과 재시도 결정은
worker의 책임이 아니다. 종료 코드는 성공 0, 처리 실패 1, 설정 오류 2, DB 오류 3, 예상 밖 오류 4다.
기존 입력 형식별 CLI와 Job worker는 별도 진입점이다.
