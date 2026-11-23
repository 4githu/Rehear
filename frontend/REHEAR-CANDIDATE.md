# Re:hear 독립 프런트 후보

Sushisite의 당시 작업 트리에서 Re:hear 화면, 로그인/가입 및 공통 라이브러리와 정적 자산을 추출한 격리 후보입니다. Sushisite 전체 라우트는 포함하지 않습니다. 원본의 이후 변경과 자동 동기화되지 않습니다.

- 화면은 루트 URL을 사용하고 기존 `/odi` 화면 링크는 쿼리를 보존해 리디렉션합니다. API `/odi/db`, `/odi/coaching`, `/odi/files`, `/odi/xreal_rehear`와 `/auth`, `/api`, `/pronunciation`은 별도로 유지합니다.
- `API_UPSTREAM_URL`은 개발 프록시가 연결할 기존 Sushisite API의 내부 주소입니다. 브라우저의 `VITE_SUSHIFASTURL`은 같은 출처 프록시를 사용하려면 비웁니다. 개발 서버는 기본 loopback에만 바인딩합니다.
- 기존 공통 인증 클라이언트와 `/auth` 경로를 보존합니다. 실제 OAuth 허용 URL, 쿠키 도메인/SameSite, 백엔드의 반환 경로 허용 정책은 별도 확인이 필요합니다. 프런트의 별도 Better Auth DB는 사용하지 않습니다.
- 검증은 합성 Firebase 공개 설정과 로컬 모의 API로만 수행했습니다. 실제 로그인, 사용자 데이터, 업로드, XR, 운영 프록시, 배포는 검증하지 않았습니다. Node 빌드 결과를 운영에 사용하기 전에 운영 프록시와 origin/헤더 신뢰 경계를 구성하고 검증해야 합니다.
- 복사 시 미디어 등 데이터 파일을 제외했습니다. 서비스에 필요한 정적 자산이 빠졌는지 실제 기능별로 확인해야 합니다.
