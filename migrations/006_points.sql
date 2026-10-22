-- 006: pontozas, XP, sorozat, jelvenyek (#34)
ALTER TABLE practice_tasks ADD COLUMN points int;            -- a dontes pontja (a dontes minosege szerint)
ALTER TABLE practice_tasks ADD COLUMN stop_atr double precision;   -- a stop tavolsaga ATR-ben (jelvenyhez)

CREATE TABLE progress (
    user_id          bigint PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    xp               int NOT NULL DEFAULT 0,
    streak           int NOT NULL DEFAULT 0,             -- egymast koveto aktiv napok (Europe/Budapest)
    best_streak      int NOT NULL DEFAULT 0,
    last_active      date,
    freeze_available boolean NOT NULL DEFAULT true       -- egy kihagyott nap "befagyaszthato"
);

CREATE TABLE user_badges (
    user_id   bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    badge     text NOT NULL,
    earned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, badge)
);
