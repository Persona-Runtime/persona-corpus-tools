# 합성 fixture

이 디렉토리의 대본은 전부 이 레포를 위해 직접 만든 합성 텍스트다.
실제 작품에서 가져온 문장은 없으며, 공개 test와 benchmark는 이 데이터만 사용한다.

`sample_series_ep001.txt`가 의도적으로 담고 있는 경우:

| 줄 | 목적 |
| --- | --- |
| 헤더 이전 2줄 | `text_before_first_header` quarantine |
| `Char A`, `A`, `Character A (whispering)` | alias 및 delivery note 처리 |
| `Character B` | 다른 persona 제외 |
| `Sign` | non-dialogue 화자 제외 |
| `[0:33 Character A` | `malformed_header` quarantine |
| 그 다음 본문 줄 | `orphaned_body_after_malformed_header` — 앞 화자에 붙지 않아야 함 |
| `Character C` | unknown 화자 제외 |
| 본문 없는 헤더 | `empty_body` quarantine |
| `[NOTICE] ...` | 대괄호로 시작하지만 헤더가 아닌 본문 |
| `[01:02:07]` | `HH:MM:SS` timestamp |
| 3줄짜리 블록 | multiline merge |
