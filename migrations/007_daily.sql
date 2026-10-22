-- 007: napi kihivas es ranglista (#35)
ALTER TABLE practice_sessions ADD COLUMN daily date;          -- a napi kihivas napja (Europe/Budapest); NULL: sima kor
CREATE UNIQUE INDEX practice_sessions_daily_key ON practice_sessions (user_id, daily) WHERE daily IS NOT NULL;
CREATE INDEX practice_sessions_daily_idx ON practice_sessions (daily) WHERE daily IS NOT NULL;

ALTER TABLE users ADD COLUMN nickname text;                    -- a ranglistan ez latszik (e-mail soha)
CREATE UNIQUE INDEX users_nickname_key ON users (lower(nickname));
