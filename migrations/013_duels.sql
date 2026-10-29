-- 013: parbaj (#55) - ket felhasznalo ugyanazon a helyzetsoron (ugyanaz a seed, a feladatkeszlet es a tipusok).
-- A meghivo az elso elfogadohoz kotodik, a kihivo visszavonhatja, a hatarido utan nem fogadhato el.
CREATE TABLE duels (
    id               text PRIMARY KEY,                 -- 12 betu: a meghivo link azonositoja
    challenger_id    bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    opponent_id      bigint REFERENCES users (id) ON DELETE SET NULL,
    seed             bigint NOT NULL,
    rounds           int NOT NULL CHECK (rounds IN (5, 10)),
    kinds            jsonb,
    pool_fingerprint text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL,
    revoked_at       timestamptz,
    settled_at       timestamptz,                      -- az eredmeny rogzitve (jelvenyek egyszer)
    winner_id        bigint REFERENCES users (id) ON DELETE SET NULL,
    rematch_of       text REFERENCES duels (id) ON DELETE SET NULL
);
CREATE INDEX duels_challenger_idx ON duels (challenger_id);
CREATE INDEX duels_opponent_idx ON duels (opponent_id);
ALTER TABLE practice_sessions ADD COLUMN duel text REFERENCES duels (id) ON DELETE SET NULL;
CREATE UNIQUE INDEX practice_sessions_duel_key ON practice_sessions (user_id, duel) WHERE duel IS NOT NULL;
