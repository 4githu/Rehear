# Re:hear

Re:hear의 프런트엔드와 백엔드를 함께 관리하는 독립 저장소입니다.

## 구성

- `frontend/`: SvelteKit 프런트. 루트 화면 경로와 기존 `/odi` API 경로를 구분합니다.
- `backend/`: FastAPI ODI·Rehear AI 코드와 기존 인증 연결 후보.
- `backend/Caddyfile`: 기존 Sushisite 인증·Rehear API·프런트를 분리하는 로컬 검증용 프록시.

이 저장소는 현재 서비스 분리를 위한 코드 후보입니다. 운영 DB·미디어·자격증명은 포함하지 않습니다. 기존 서비스와 Quest의 공개 주소를 변경하지 않았습니다.

## 개발 및 검증

프런트는 `cd frontend && npm ci` 후 `npm run dev`로 시작합니다. API 연결은 `API_UPSTREAM_URL`로 지정합니다. Node 배포 후보는 `BUILD_TARGET=node npm run build`입니다. Firebase 설정이 필요한 경우 실제 서비스의 공개 설정을 별도로 제공합니다. 인증 비밀값을 프런트에 넣지 않습니다.

백엔드 의존성은 `backend/requirements.txt`, EVC 의존성은 `backend/requirements-evc*.txt`를 확인합니다. 독립 가상환경과 격리된 테스트 데이터를 사용합니다. Google OAuth는 기존 Sushisite 인증 서비스에 연결하는 구조이며 이 후보가 해당 운영 인증 서버를 대체하지 않습니다.

`node frontend/tests/isolation/routing.test.mjs`로 화면/API 경계 검사를 실행합니다. `backend/tests/test_ingress_contract.py`는 실제 프런트·백엔드·Caddy 및 모의 인증 서버를 loopback에서 검증합니다. 프런트 의존성과 Caddy가 필요하며 `TMPDIR`은 별도 임시 폴더여야 합니다. 운영 환경 파일이나 데이터를 공급하지 않습니다.

## 현재 검증 범위와 남은 작업

로컬 빌드와 `/odi` 화면 리디렉션, 모의 Google 오류 복귀, Quest API 라우팅을 검증하는 테스트가 포함돼 있습니다. 실제 Google 로그인 성공·Quest 기기 호출·운영 데이터 이전 및 복구·자동 배포와 롤백은 별도 후속 작업입니다. DB 이전이나 운영 전환이 끝났다는 의미는 아닙니다.

프런트 분리 상세는 `frontend/REHEAR-CANDIDATE.md`, 백엔드의 인증·시작·데이터 경계는 `backend/README.md`를 확인하세요. 공통 라이브러리는 호환성을 위해 일부 유지되어 있으며, 사용하지 않는 의존성 정리는 후속 범위입니다. 스냅샷은 원래 Sushisite 작업 트리와 자동 동기화되지 않습니다.

## 포함하지 않은 항목

환경 파일, DB, 업로드, 로그, 빌드 결과, node_modules와 번역 도구 캐시, 관계없는 설치 파일 및 코드 덤프는 제외합니다. 폰트와 기존 자산에 딸린 저작권·라이선스 고지는 보존합니다. 이 비공개 저장소 게시만으로 제3자 자산의 재배포 라이선스를 새로 부여하지 않습니다.
