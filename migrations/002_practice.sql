-- 002: gyakorlo korok felhasznalonkent (#32). A kozos feladatkeszlet fajl marad (data/practice/pool.json.gz).

CREATE TABLE practice_sessions (
    id               text PRIMARY KEY,           -- 12 betu (practice/store.new_id)
    user_id          bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at       timestamptz NOT NULL DEFAULT now(),
    seed             bigint NOT NULL,
    rounds           int NOT NULL,
    mix              jsonb NOT NULL,
    pool_fingerprint text NOT NULL,
    code             text NOT NULL,              -- megosztasi kod
    warning          text,
    summary          jsonb                       -- a kor-statisztika, ha minden dontes megvan
);
CREATE INDEX practice_sessions_user_idx ON practice_sessions (user_id, created_at DESC);

-- egy sor = egy helyzet; a dontes es az itelet oszlopban (statisztikahoz), a felfedes tomoritett JSON-ban
CREATE TABLE practice_tasks (
    session_id text NOT NULL REFERENCES practice_sessions (id) ON DELETE CASCADE,
    k          int NOT NULL,
    task_id    text NOT NULL,
    symbol     text NOT NULL,
    day        date NOT NULL,
    category   text NOT NULL,
    source     text NOT NULL,
    breakout   jsonb,
    decision   jsonb,
    verdict    text,                              -- good / lucky / unlucky / bad
    r          double precision,
    decided_at timestamptz,
    result     bytea,                             -- gzip JSON: a felfedes (jovobeli gyertyak, levezetes)
    PRIMARY KEY (session_id, k)
);
