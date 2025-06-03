# models.py
from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey, DateTime, Enum, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime, timezone, timedelta
import enum


class Position(str, enum.Enum):
    TOP = "TOP"
    JUNGLE = "JUNGLE"
    MID = "MID"
    ADC = "ADC"
    SUPPORT = "SUPPORT"
    ALL = "ALL"


class Tier(str, enum.Enum):
    IRON = "IRON"
    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    PLATINUM = "PLATINUM"
    EMERALD = "EMERALD"
    DIAMOND = "DIAMOND"
    MASTER = "MASTER"
    GRANDMASTER = "GRANDMASTER"
    CHALLENGER = "CHALLENGER"


# 사용자 모델
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True)
    hashed_password = Column(String)


# 플레이어 모델
class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, index=True)
    nickname = Column(String, index=True) # 닉네임 중복 가능하도록 unique=False 또는 별도 처리
    tier = Column(Enum(Tier), default=Tier.SILVER)
    division = Column(Integer, default=4)
    position = Column(Enum(Position))  # 주 포지션
    sub_position = Column(Enum(Position), nullable=True)  # 부 포지션, 선택 사항
    lp = Column(Integer, default=0)
    tier_score = Column(Float, default=0.0)
    match_score = Column(Float, default=1000.0) # 초기값은 tier_score와 동기화 될 수 있음
    win_count = Column(Integer, default=0) # 전체 승리
    lose_count = Column(Integer, default=0) # 전체 패배
    created_at = Column(DateTime, default=datetime.utcnow)

    team_assignments = relationship("TeamAssignment", back_populates="player")

    # __table_args__ = (UniqueConstraint('nickname', name='_nickname_uc'),) # 필요시 닉네임 유니크 제약


# 매치 모델
class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    match_date = Column(DateTime, default=lambda: datetime.now(timezone(timedelta(hours=9))))
    blue_team_avg_score = Column(Float) # tier_score 평균
    red_team_avg_score = Column(Float)  # tier_score 평균
    blue_team_match_score = Column(Float) # match_score 평균
    red_team_match_score = Column(Float)  # match_score 평균
    balance_score = Column(Float) # abs(blue_team_avg_score - red_team_avg_score)
    winner = Column(String, nullable=True) # "BLUE" or "RED"
    is_completed = Column(Boolean, default=False)

    team_assignments = relationship("TeamAssignment", back_populates="match")


# 팀 배정 모델
class TeamAssignment(Base):
    __tablename__ = "team_assignments"

    id = Column(Integer, primary_key=True, index=True)
    team = Column(String)  # "BLUE" or "RED"
    assigned_position = Column(Enum(Position), nullable=False) # 플레이어가 배정된 실제 라인

    match_id = Column(Integer, ForeignKey("matches.id"))
    player_id = Column(Integer, ForeignKey("players.id"))

    match = relationship("Match", back_populates="team_assignments")
    player = relationship("Player", back_populates="team_assignments")