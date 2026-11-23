# 홈서버 배포

PR은 GitHub의 격리된 러너에서 검사하고, 성공한 `main`의 push CI와 현재 원격 SHA가 일치할 때만 Mac이 새 릴리스를 빌드·전환합니다. PR 코드나 실패한 main을 홈서버에서 실행하지 않습니다. 병합된 main은 서버가 실행하도록 허용한 신뢰된 코드입니다. 빌드에는 운영 비밀을 환경변수로 넘기지 않으며, 이 구성은 같은 OS 사용자 내부의 보안 샌드박스를 뜻하지 않습니다.

`deploy.py`는 파일 잠금으로 배포를 직렬화합니다. 빌드를 먼저 끝내고 최근 발표·분석 작업이 없는지 확인한 뒤 API를 잠시 점검 상태로 바꿉니다. 기존 프로세스의 종료를 확인하고 DB·미디어를 백업한 다음 새 서버를 기동합니다. 기동·프런트·API·리비전 검증에 실패하면 이전 코드를 다시 실행합니다. 오래된 DB를 최신 데이터 위에 자동 복원하지 않습니다. 롤백한 SHA는 새 main 또는 명시적인 `--retry`까지 자동 재배포하지 않습니다.

런타임 폴더에는 `config.json`(경로), `backend.json`(서버 설정, 0600), `frontend.json`(공개 빌드 설정·기존 프런트 인증 설정, 0600), `data/`(ODI DB와 미디어), `releases/<SHA>/`, `backups/`, `logs/`가 있습니다. 이 폴더는 Git에 넣지 않습니다. 기존 Sushi 주 인증 서버와 인증 DB는 계속 사용합니다. 기존 ODI 주소도 선택된 서버로 전달해 이전 코드의 별도 DB 쓰기를 막습니다.

설치는 기존 python-dotenv가 있는 운영 Python으로 `machine.py configure --home <runtime> --legacy-backend <sushi-fast> --legacy-frontend <sushi-app>`, 이어서 `stage`, `cutover` 순서입니다. `stage`는 원래 서버로 계속 전달하고, `cutover`만 최종 데이터 이동·서비스 전환을 합니다. 최초 전환은 운영자의 명시적인 실행이며 폴러가 임의로 시작하지 않습니다.

일상 운영은 `python <runtime>/bin/deploy.py --config <runtime>/config.json status|poll|deploy|rollback`입니다. `app.rehear.autodeploy` LaunchAgent가 로그인된 홈서버에서 60초마다 확인하며, Mac이 꺼져 있으면 다음 기동 때 확인합니다. 실제 선택 리비전은 `https://rehear.kr/_rehear/deployment`에서 확인합니다. 내부 `/_ops/*`는 공개 프록시에서 차단합니다.

외부 호스팅으로 옮길 때도 `frontend: BUILD_TARGET=node npm run build`, `backend: uvicorn main:app --workers 1`, `REHEAR_ODI_DB_PATH`, `REHEAR_DATA_ROOT` 및 기존 `/auth` 연결 계약을 재사용할 수 있습니다. Vercel 프런트 어댑터도 기존 설정에 남아 있습니다. SQLite·업로드·인메모리 EVC를 쓰는 현재 백엔드를 서버리스로 그대로 복사하는 구성은 아닙니다.

배포 성공 후 활성·이전 릴리스와 최신 후보를 남기고 오래된 생성 릴리스를 정리합니다. 일반 배포 백업은 최근 3개를 보존하며 최초 이관 백업과 표준 이름 밖의 파일은 자동 삭제하지 않습니다. 사용자 DB·현재 미디어는 이 정리 대상에 포함하지 않습니다.
