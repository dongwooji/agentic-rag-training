# Groundwork frontend demo

React + TypeScript + Vite로 기존 FastAPI를 사용하는 로컬 데모입니다. 질문 입력, 추가 확인, 답변, 사용 문헌과 실행 근거를 제공합니다. 백엔드와 semantic routing 정책은 변경하지 않습니다.

## 실행

Node.js 22.12 이상을 사용합니다. 저장소 루트에서:

```powershell
cd frontend
npm ci
npm run dev
```

<http://127.0.0.1:5173/>에서 열립니다. 개발 서버의 `/api`는 기본적으로 `http://127.0.0.1:8000`으로 전달합니다. 백엔드는 별도 터미널에서 저장소 README에 따라 실행합니다. 후보 선택은 서버의 `AGENTIC_RAG_QUESTION_INTERPRETATION=phase_a` 설정이 필요합니다.

주소를 바꾸려면 `frontend/.env.local`에 공개 설정만 작성합니다.

```dotenv
API_PROXY_TARGET=http://127.0.0.1:8000
```

`VITE_API_BASE_URL`은 브라우저가 직접 접근할 API URL입니다. 기본값은 `/api`입니다. 다른 origin을 지정하면 해당 서버의 CORS 지원이 필요합니다. `npm run build`의 정적 결과를 배포할 때는 `/api` reverse proxy를 별도로 구성해야 합니다. Vite 개발용 proxy는 정적 빌드·preview에 적용되지 않습니다. 인증키·DB 비밀번호는 프론트엔드 설정에 넣지 않습니다.

## 사용자 흐름

- 새로 작성한 예시 질문은 클릭하면 입력칸에 채워지고, 사용자가 전송합니다. 기존 평가셋은 읽거나 가져오지 않습니다.
- 모든 신규 `POST /query`와 `POST /query/clarify`는 `debug: true`를 전송합니다.
- 후보가 있는 확인 요청은 반환된 option ID를 `/query/clarify`로 보냅니다.
- 후보 없는 확인 요청은 추가 입력칸을 표시합니다. 원 질문에 `추가 확인 답변:`과 답변을 누적해 `/query`로 다시 제출합니다. 합계 4,000자를 검사합니다.
- 운동 후보 선택 요청에는 `clarification_id`, `selected_option`, `debug: true`를 보냅니다. 선택 후 반환된 답변의 trace로 실행 근거를 표시하며, 없는 trace를 추측하거나 이전 응답에서 복사하지 않습니다.
- HTTP 성공과 실행 결과를 구분합니다. `execution_failure`, 근거 부족, 확인 필요, 네트워크 오류, 후보 만료를 각각 처리합니다.
- 자동 POST 재시도는 없습니다. 전송 중 중복 요청을 막으며, 대기 중지는 브라우저 요청만 중단합니다. 서버 실행 취소를 보장하지 않습니다.
- 각 질문은 독립 실행입니다. 화면 이력은 메모리에만 남고 새 시작·모드 변경·새로고침 때 초기화됩니다.
- API 연결 표시는 liveness 확인 결과입니다. DB·LLM 준비 상태는 보장하지 않습니다.

## 실제 서버 응답 녹화

배포된 데모 응답을 임의 생성하지 않습니다. 현재 포함된 `recordings/format-example.json` 한 개는 화면에 **형식 예시 / 실행일 없음 / 실제 서버 실행 아님**으로 표시합니다.

실제 서버가 준비된 뒤 UTF-8 질문 파일을 작성하고 녹화합니다. 경로는 frontend 기준입니다.

```powershell
npm run record -- --question-file C:/temp/question.txt --output recordings/my-run.json --title "운동 기록과 문헌 확인"
```

기본 서버 주소는 `http://127.0.0.1:8000`이며 `--base-url`로 바꿀 수 있습니다. 확인 요청이 돌아오면 터미널에서 후보 ID 또는 추가 답변을 입력합니다. 빈 입력은 완료된 단계까지 저장하고 종료합니다. 비대화식 실행에서는 `--answers-file C:/temp/answers.json`으로 추가 답변/후보 ID의 JSON 문자열 배열을 전달합니다. 배열이 소진되면 종료합니다.

스크립트는 실제 HTTP 응답 JSON을 필드 보충 없이 저장하고, 매 단계 완료 후 파일을 갱신합니다. 첫 요청 시작 시각을 ISO 8601 UTC로 기록합니다. 기존 출력 파일은 덮어쓰지 않습니다. 실제 녹화 JSON은 기본적으로 Git에서 제외됩니다. 검토한 녹화를 배포에 포함하려면 별도로 명시적으로 추가합니다. 폴더의 JSON은 빌드에 포함되므로 공개 전 내용 확인이 필요합니다.

### 파일 형식 v1

검증 계약은 `src/lib/contracts.ts`의 `recordingSchema`, 전체 예시는 `recordings/format-example.json`입니다.

| 필드             | 의미                                              |
| ---------------- | ------------------------------------------------- |
| schema_version   | 고정값 agentic_rag_recording_v1                   |
| id               | 녹화의 고유 식별자                                |
| title            | 선택 목록 표시 이름                               |
| kind             | recorded 또는 format_example                      |
| recorded_at      | 첫 실제 요청 시작 시각 ISO 8601. 형식 예시는 null |
| steps            | 실제 요청·응답의 순서 있는 배열                   |
| steps[].request  | endpoint와 그대로 전송한 body                     |
| steps[].response | 실제 서버의 HTTP 200 QueryResponse JSON           |

첫 단계는 `/query`입니다. 추가 확인을 포함한 `/query`에는 debug=true가 필수입니다. 신규 `/query/clarify` 녹화에는 clarification_id, selected_option, debug=true를 그대로 저장합니다. 이전 v1 녹화의 debug 없는 후보 선택 요청도 계속 읽고 재생하며, 기존 요청·응답에 debug나 trace를 추가하지 않습니다. recorded는 실행일이 필수이며 format_example에는 실행일을 넣을 수 없습니다.

### 재생

파일을 추가한 뒤 개발 서버를 다시 시작하거나 재빌드하고 **녹화 재생** 모드를 선택합니다. 실제 녹화에는 **녹화된 응답**과 한국 시간 실행일을 표시합니다. 선택한 녹화의 다음 단계와 정확히 일치하는 입력만 재생하며, 임의 질문을 저장된 답변에 매핑하지 않습니다. 다른 입력은 현재 단계를 소비하지 않고 안내합니다. 후보 선택과 텍스트 확인 답변도 같은 순서로 재생합니다. 재생은 API 호출·LLM·DB 조회를 하지 않으며, 녹화된 후보의 TTL을 다시 적용하지 않습니다.

유효하지 않은 파일이나 중복 ID는 오류 목록으로 표시합니다. 응답을 자동 합성하거나 실제 서버 실패를 녹화 모드로 대체하지 않습니다.

## 실행 근거와 현재 API 한계

- `used_literature_chunk_ids`를 기준으로, trace의 final_evidence/retrievals에서 제목과 섹션을 찾아 우선 표시합니다. 청크 ID는 펼쳐서 확인합니다. 없는 제목·섹션은 미제공으로 표시합니다.
- Tool, Grader 실행 성공/실패와 요구사항별 충족 여부, Recovery 횟수와 검색어 전/후를 표시합니다.
- 프론트엔드가 읽는 `trace.interpreter.parsing_source`가 없으면 규칙 parser/LLM Interpreter를 추측하지 않고 **응답에 제공되지 않음**으로 표시합니다. Phase A의 중첩된 `phase_a.parsing_source` 표시는 현재 지원하지 않습니다.
- `/query/clarify`도 debug를 지원합니다. 후보 선택 후 답변의 실행 근거 패널에서 반환된 문헌·Tool·Grader·Recovery 정보를 확인할 수 있습니다. trace가 없는 응답이나 과거 녹화는 해당 정보를 미제공으로 표시합니다.
- 문헌 원문·excerpt·DOI/URL은 공개 응답에 없습니다. 논문 링크나 발췌문은 만들지 않습니다.
- 제목·섹션 필드는 nullable이며 사용한 청크의 metadata가 항상 보장되지는 않습니다.
- 실시간 단계 이벤트가 없어 완료 후 근거를 표시합니다.

## 검증

```powershell
npm test
npm run build
npm run lint
```

테스트의 통제 응답은 `src/test/`에만 있고 데모 녹화에 포함되지 않습니다. UI 확인, 실제 서버 녹화의 저장·재생, 오류 처리와 provenance를 테스트합니다. 실제 연구 데이터·LLM 실행은 별도로 준비된 서버가 있어야 확인할 수 있습니다.
