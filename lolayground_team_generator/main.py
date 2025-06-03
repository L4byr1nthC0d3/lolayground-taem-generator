# main.py
from fastapi import FastAPI, Depends, HTTPException, status, Request, Form, Body
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
import os
import uvicorn

from database import SessionLocal, engine, Base
import models
import schemas
from utils import calculate_tier_score, balance_teams, update_team_match_scores, POSITIONS_ORDER

# 데이터베이스 초기화
Base.metadata.create_all(bind=engine)

app = FastAPI(title="LoL 팀 매칭 시스템")

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-is-still-secret-but-use-env-var")  # 환경 변수 사용 권장
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 120

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Qv4RDGoEE8G41ru")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_admin_user_on_startup(db: Session):
    admin_user = db.query(models.User).filter(models.User.user_id == ADMIN_USER_ID).first()
    if not admin_user:
        hashed_password = get_password_hash(ADMIN_PASSWORD)
        admin = models.User(user_id=ADMIN_USER_ID, hashed_password=hashed_password)
        db.add(admin)
        try:
            db.commit()
            print(f"Admin user '{ADMIN_USER_ID}' created.")
        except IntegrityError:
            db.rollback()
            print(f"Admin user '{ADMIN_USER_ID}' already exists or error during creation.")
        except Exception as e:
            db.rollback()
            print(f"Error creating admin user: {e}")
    elif not verify_password(ADMIN_PASSWORD, admin_user.hashed_password):
        # 비밀번호가 변경된 경우 업데이트 (주의: 실제 운영 환경에서는 더 안전한 방법 고려)
        admin_user.hashed_password = get_password_hash(ADMIN_PASSWORD)
        try:
            db.commit()
            print(f"Admin user '{ADMIN_USER_ID}' password updated.")
        except Exception as e:
            db.rollback()
            print(f"Error updating admin password: {e}")
    else:
        print(f"Admin user '{ADMIN_USER_ID}' verified.")


@app.on_event("startup")
async def startup_event():
    db = SessionLocal()
    try:
        create_admin_user_on_startup(db)
    finally:
        db.close()


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def get_user_from_db(db, user_id: str):
    return db.query(models.User).filter(models.User.user_id == user_id).first()


def authenticate_user(db, user_id: str, password: str):
    user = get_user_from_db(db, user_id)
    if not user or not verify_password(password, user.hashed_password) or user.user_id != ADMIN_USER_ID:
        return False
    return user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire_time = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire_time})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user_from_cookie(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token or not token.startswith("Bearer "): return None
    token = token.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None: return None
    except JWTError:
        return None
    return get_user_from_db(db, user_id=user_id)


async def login_required(user: models.User = Depends(get_current_user_from_cookie)):
    if user is None or user.user_id != ADMIN_USER_ID:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            detail="로그인이 필요합니다",
            headers={"Location": "/login"},
        )
    return user


@app.get("/", response_class=HTMLResponse, name="home")
async def home(request: Request, db: Session = Depends(get_db)):
    user = await get_current_user_from_cookie(request, db)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@app.get("/login", response_class=HTMLResponse, name="login_page")
async def login_page(request: Request, db: Session = Depends(get_db)):
    user = await get_current_user_from_cookie(request, db)
    if user and user.user_id == ADMIN_USER_ID:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("login.html", {"request": request, "error": request.query_params.get("error")})


@app.post("/login", name="login")
async def login_form(request: Request, user_id: str = Form(...), password: str = Form(...),
                     db: Session = Depends(get_db)):
    if user_id != ADMIN_USER_ID:
        return templates.TemplateResponse("login.html", {"request": request, "error": "관리자 아이디가 아닙니다."})

    user = authenticate_user(db, user_id, password)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "아이디 또는 비밀번호가 잘못되었습니다"})

    access_token = create_access_token(data={"sub": user.user_id})
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True,
                        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60, samesite="Lax",
                        secure=request.url.scheme == "https")  # secure 추가
    return response


@app.get("/logout", name="logout")
async def logout(request: Request):  # request 추가
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="access_token", samesite="Lax",
                           secure=request.url.scheme == "https")  # samesite, secure 추가
    return response


# --- 플레이어 관련 엔드포인트 ---
@app.get("/player-management", response_class=HTMLResponse, name="player_management_page")
async def player_management_page(request: Request, admin: models.User = Depends(login_required),
                                 db: Session = Depends(get_db)):
    players = db.query(models.Player).order_by(models.Player.nickname).all()
    return templates.TemplateResponse(
        "player_management.html",
        {"request": request, "user": admin, "players": players, "Position": models.Position}
    )


@app.post("/players/", response_model=schemas.Player, name="create_player_api", status_code=status.HTTP_201_CREATED)
def create_player_api(player: schemas.PlayerCreate, db: Session = Depends(get_db),
                      admin: models.User = Depends(login_required)):
    # 닉네임 중복 검사 (DB 레벨에서도 UniqueConstraint를 거는 것이 좋음)
    existing_player = db.query(models.Player).filter(models.Player.nickname == player.nickname).first()
    if existing_player:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="이미 사용 중인 닉네임입니다.")

    tier_score = calculate_tier_score(player.tier, player.division, player.lp)
    db_player = models.Player(
        nickname=player.nickname,
        tier=player.tier,
        division=player.division,
        position=player.position,
        sub_position=player.sub_position,
        lp=player.lp,
        tier_score=tier_score,
        match_score=tier_score,  # 초기 매칭 점수는 티어 점수와 동일하게
        win_count=0, lose_count=0
    )
    try:
        db.add(db_player)
        db.commit()
        db.refresh(db_player)
        return db_player
    except IntegrityError as e:
        db.rollback()
        # IntegrityError의 원인(예: UniqueConstraint 위반)을 좀 더 자세히 파악
        detail_msg = "플레이어 저장 중 오류 발생"
        if "UNIQUE constraint failed" in str(e.orig):  # SQLite의 경우
            detail_msg = "닉네임 또는 다른 고유 값이 중복됩니다."  # 실제로는 어떤 필드인지 명시
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail_msg)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"서버 오류: {str(e)}")


@app.put("/players/{player_id}", response_model=schemas.Player, name="update_player_api")
def update_player_api(player_id: int, player_data: schemas.PlayerCreate, db: Session = Depends(get_db),
                      admin: models.User = Depends(login_required)):
    player_in_db = db.query(models.Player).filter(models.Player.id == player_id).first()
    if not player_in_db:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="플레이어를 찾을 수 없습니다")

    # 닉네임 변경 시 중복 검사
    if player_data.nickname != player_in_db.nickname:
        existing_player = db.query(models.Player).filter(models.Player.nickname == player_data.nickname,
                                                         models.Player.id != player_id).first()
        if existing_player:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="이미 사용 중인 닉네임입니다.")

    tier_score = calculate_tier_score(player_data.tier, player_data.division, player_data.lp)

    player_in_db.nickname = player_data.nickname
    player_in_db.tier = player_data.tier
    player_in_db.division = player_data.division
    player_in_db.position = player_data.position
    player_in_db.sub_position = player_data.sub_position
    player_in_db.lp = player_data.lp
    player_in_db.tier_score = tier_score
    # 티어 변경 시 match_score를 tier_score로 리셋할지 여부 결정 필요. 여기서는 유지.

    try:
        db.commit()
        db.refresh(player_in_db)
        return player_in_db
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="닉네임 또는 다른 고유 값이 중복됩니다.")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"서버 오류: {str(e)}")


@app.delete("/players/{player_id}", name="delete_player_api", status_code=status.HTTP_200_OK)
def delete_player_api(player_id: int, db: Session = Depends(get_db), admin: models.User = Depends(login_required)):
    player = db.query(models.Player).filter(models.Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="플레이어를 찾을 수 없습니다")

    try:
        # 관련된 TeamAssignment 레코드 먼저 삭제 (ON DELETE CASCADE가 설정되지 않은 경우)
        db.query(models.TeamAssignment).filter(models.TeamAssignment.player_id == player_id).delete(
            synchronize_session=False)
        # PlayerPositionStats 같은 연관 데이터도 있다면 삭제 로직 추가
        db.delete(player)
        db.commit()
        return {"message": "플레이어가 삭제되었습니다", "player_id": player_id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"플레이어 삭제 중 오류 발생: {str(e)}")


# --- 매치 관련 엔드포인트 ---
@app.get("/match-maker", response_class=HTMLResponse, name="match_maker_page")
async def match_maker_page(request: Request, admin: models.User = Depends(login_required),
                           db: Session = Depends(get_db)):
    players = db.query(models.Player).order_by(models.Player.nickname).all()
    recent_matches = db.query(models.Match).order_by(models.Match.match_date.desc()).limit(10).all()
    return templates.TemplateResponse(
        "match_maker.html",
        {"request": request, "user": admin, "players": players, "recent_matches": recent_matches}
    )


@app.get("/matches/", response_model=List[schemas.Match], name="get_matches_api", tags=["api_match"])
def get_matches_api(db: Session = Depends(get_db), admin: models.User = Depends(login_required)):
    matches_orm = db.query(models.Match).order_by(models.Match.match_date.desc()).limit(20).all()
    return [schemas.Match.from_orm(match) for match in matches_orm]


@app.post("/match/", response_model=schemas.MatchWithTeams, name="create_match_api",
          status_code=status.HTTP_201_CREATED)
def create_match_api(player_ids: List[int] = Body(..., embed=True), db: Session = Depends(get_db),
                     admin: models.User = Depends(login_required)):
    if len(player_ids) != 10:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="정확히 10명의 플레이어가 필요합니다")

    players_in_db = db.query(models.Player).filter(models.Player.id.in_(player_ids)).all()
    if len(players_in_db) != 10:
        found_ids = {p.id for p in players_in_db}
        missing_ids = set(player_ids) - found_ids
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"일부 플레이어를 찾을 수 없습니다. (ID: {list(missing_ids)})")

    try:
        # balance_teams는 이제 각 팀 플레이어 리스트를 (TOP, JGL, MID, ADC, SUP) 순서로 반환
        blue_team_ordered_players, red_team_ordered_players = balance_teams(players_in_db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # 팀 평균 '티어 점수' 및 '매치 점수' 계산
    blue_avg_tier_score = sum(p.tier_score for p in blue_team_ordered_players) / 5 if blue_team_ordered_players else 0
    red_avg_tier_score = sum(p.tier_score for p in red_team_ordered_players) / 5 if red_team_ordered_players else 0
    blue_avg_match_score = sum(p.match_score for p in blue_team_ordered_players) / 5 if blue_team_ordered_players else 0
    red_avg_match_score = sum(p.match_score for p in red_team_ordered_players) / 5 if red_team_ordered_players else 0

    balance_score_val = abs(blue_avg_tier_score - red_avg_tier_score)
    KST = timezone(timedelta(hours=9))

    db_match = models.Match(
        blue_team_avg_score=blue_avg_tier_score,
        red_team_avg_score=red_avg_tier_score,
        blue_team_match_score=blue_avg_match_score,
        red_team_match_score=red_avg_match_score,
        balance_score=balance_score_val,
        match_date=datetime.now(KST)
    )
    try:
        db.add(db_match)
        db.flush()  # match.id를 얻기 위해 flush

        # TeamAssignment 생성 시, 배정된 라인 정보(POSITIONS_ORDER) 사용
        for i, player_obj in enumerate(blue_team_ordered_players):
            assigned_pos = POSITIONS_ORDER[i]
            db.add(models.TeamAssignment(team="BLUE", match_id=db_match.id, player_id=player_obj.id,
                                         assigned_position=assigned_pos))

        for i, player_obj in enumerate(red_team_ordered_players):
            assigned_pos = POSITIONS_ORDER[i]
            db.add(models.TeamAssignment(team="RED", match_id=db_match.id, player_id=player_obj.id,
                                         assigned_position=assigned_pos))

        db.commit()
        db.refresh(db_match)

        blue_team_schema = [schemas.Player.from_orm(p) for p in blue_team_ordered_players]
        red_team_schema = [schemas.Player.from_orm(p) for p in red_team_ordered_players]

        return schemas.MatchWithTeams(
            id=db_match.id, match_date=db_match.match_date,
            blue_team_avg_score=db_match.blue_team_avg_score, red_team_avg_score=db_match.red_team_avg_score,
            blue_team_match_score=db_match.blue_team_match_score, red_team_match_score=db_match.red_team_match_score,
            balance_score=db_match.balance_score, winner=db_match.winner, is_completed=db_match.is_completed,
            blue_team=blue_team_schema, red_team=red_team_schema
        )
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"매치 저장 중 오류: {str(e)}")


@app.get("/match/{match_id}", response_class=HTMLResponse, name="match_detail_page")
async def match_detail_page(match_id: int, request: Request, admin: models.User = Depends(login_required),
                            db: Session = Depends(get_db)):
    match = db.query(models.Match).filter(models.Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="매치를 찾을 수 없습니다")

    # TeamAssignment에서 플레이어 ID와 '배정된 포지션'을 함께 가져와 순서대로 정렬
    # (create_match_api에서 POSITIONS_ORDER 순서대로 TeamAssignment를 만들었다고 가정,
    #  assigned_position 값으로 정렬하거나, TeamAssignment.id 순으로 정렬하여 순서 유추)

    # 여기서는 assigned_position enum 순서대로 정렬하는 것이 더 안정적.
    # POSITIONS_ORDER 리스트의 순서대로 정렬하려면 CASE 문 등을 사용해야 함.
    # 간단하게는 TeamAssignment.id 순으로 가져온 뒤, 프론트에서 POSITIONS_ORDER를 참조해 표시.

    blue_assignments = db.query(models.TeamAssignment).filter_by(match_id=match_id, team="BLUE").order_by(
        models.TeamAssignment.id).all()
    red_assignments = db.query(models.TeamAssignment).filter_by(match_id=match_id, team="RED").order_by(
        models.TeamAssignment.id).all()

    blue_team_players_with_assigned_pos = []
    for ta in blue_assignments:
        player = db.query(models.Player).get(ta.player_id)
        if player:
            blue_team_players_with_assigned_pos.append(
                {"player": player, "assigned_pos_value": ta.assigned_position.value})

    red_team_players_with_assigned_pos = []
    for ta in red_assignments:
        player = db.query(models.Player).get(ta.player_id)
        if player:
            red_team_players_with_assigned_pos.append(
                {"player": player, "assigned_pos_value": ta.assigned_position.value})

    # POSITIONS_ORDER를 템플릿에 전달하여, 해당 순서대로 플레이어를 표시하도록 함
    # blue_team_players_with_assigned_pos 리스트 자체는 create_match_api에서 생성된 순서(라인순서)를 따름.

    return templates.TemplateResponse(
        "match_detail.html",
        {
            "request": request, "user": admin, "match": match,
            "blue_team_info": blue_team_players_with_assigned_pos,  # assigned_pos_value 포함
            "red_team_info": red_team_players_with_assigned_pos,  # assigned_pos_value 포함
            "positions_order": [p.value for p in POSITIONS_ORDER]  # 템플릿에서 라인 이름 표시용 (문자열 리스트)
        }
    )


@app.post("/match/{match_id}/result", name="record_match_result_api", status_code=status.HTTP_200_OK)
def record_match_result_api(match_id: int, result: schemas.MatchResult, db: Session = Depends(get_db),
                            admin: models.User = Depends(login_required)):
    match = db.query(models.Match).filter(models.Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="매치를 찾을 수 없습니다")
    if match.is_completed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="이미 결과가 등록된 매치입니다")
    if result.winner not in ["BLUE", "RED"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="승리 팀은 BLUE 또는 RED여야 합니다")

    success = update_team_match_scores(result.winner, match_id, db)
    if not success:
        # update_team_match_scores 내부에서 False 반환 시 (예: 매치 없음, 이미 완료됨)
        # 실제로는 그 전에 위에서 체크되므로, 여기 도달은 드묾.
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="점수 업데이트 중 오류 발생")

    # 매치 점수 변경 후, Match 객체의 팀 평균 매치 점수도 업데이트 (선택적)
    # blue_assignments = db.query(models.TeamAssignment).filter_by(match_id=match_id, team="BLUE").all()
    # red_assignments = db.query(models.TeamAssignment).filter_by(match_id=match_id, team="RED").all()
    # blue_players = [db.query(models.Player).get(ta.player_id) for ta in blue_assignments if db.query(models.Player).get(ta.player_id)]
    # red_players = [db.query(models.Player).get(ta.player_id) for ta in red_assignments if db.query(models.Player).get(ta.player_id)]
    # if blue_players: match.blue_team_match_score = sum(p.match_score for p in blue_players) / len(blue_players)
    # if red_players: match.red_team_match_score = sum(p.match_score for p in red_players) / len(red_players)
    # db.commit()
    # db.refresh(match) # Match 객체에 변경사항 반영

    return {"message": f"{result.winner} 팀 승리! 결과가 성공적으로 등록되었습니다."}


# --- 플레이어 통계 ---
@app.get("/player-stats", response_class=HTMLResponse, name="player_stats_page")
async def player_stats_page(request: Request, admin: models.User = Depends(login_required),
                            db: Session = Depends(get_db), sort_by: str = "match_score", order: str = "desc"):
    players = db.query(models.Player).all()
    player_stats_data = []
    for player in players:
        total_games = player.win_count + player.lose_count
        win_rate = (player.win_count / total_games * 100) if total_games > 0 else 0

        position_str = player.position.value if player.position else "N/A"
        sub_position_str = player.sub_position.value if player.sub_position else "없음"
        tier_str = player.tier.value if player.tier else "N/A"

        player_stats_data.append({
            "player": player, "total_games": total_games, "win_rate": win_rate,
            "clean_position": position_str,
            "clean_sub_position": sub_position_str,
            "clean_tier": tier_str
        })

    reverse = True if order == "desc" else False
    # 정렬 기준 필드 이름 일치 확인
    if sort_by == "match_score":
        player_stats_data.sort(key=lambda x: x["player"].match_score, reverse=reverse)
    elif sort_by == "total_games":
        player_stats_data.sort(key=lambda x: x["total_games"], reverse=reverse)
    elif sort_by == "win_rate":
        player_stats_data.sort(key=lambda x: x["win_rate"], reverse=reverse)
    # 추가 정렬 기준이 있다면 여기에 추가

    return templates.TemplateResponse(
        "player_stats.html",
        {"request": request, "user": admin, "player_stats": player_stats_data, "sort_by": sort_by, "order": order}
    )


# 도움말 페이지
@app.get("/help", response_class=HTMLResponse, name="help_page")
async def help_page(request: Request, db: Session = Depends(get_db)):  # db 의존성 추가
    user = await get_current_user_from_cookie(request, db)
    return templates.TemplateResponse("help.html", {"request": request, "user": user})


# --- API 문서용 엔드포인트 ---
@app.post("/token", response_model=schemas.Token, tags=["api_auth"])
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    if form_data.username != ADMIN_USER_ID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="관리자 아이디가 아닙니다",
                            headers={"WWW-Authenticate": "Bearer"})

    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="아이디 또는 비밀번호가 잘못되었습니다",
                            headers={"WWW-Authenticate": "Bearer"})

    access_token = create_access_token(data={"sub": user.user_id})
    return {"access_token": access_token, "token_type": "bearer"}


async def get_current_api_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증할 수 없습니다",
                                          headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None: raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = get_user_from_db(db, user_id=user_id)
    if user is None or user.user_id != ADMIN_USER_ID:  # API 사용자는 관리자여야 함
        raise credentials_exception
    return user


@app.get("/api/users/me", response_model=schemas.User, tags=["api_auth"])
async def read_users_me_api(current_user: models.User = Depends(get_current_api_user)):
    return current_user


if __name__ == "__main__":
    # 실제 운영시에는 uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4 등으로 실행
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)