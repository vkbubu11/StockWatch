-- 012: megosztható eredménykártya (#57). A megosztás pillanatában rögzített adat (becenév, eredmény, jelölések);
-- csak a felhasználó kifejezett megosztására jön létre, visszavonható; e-mail-cím nem kerül bele.
CREATE TABLE shares (
    id         text PRIMARY KEY,                 -- 12 betu (practice/store.new_id)
    user_id    bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    kind       text NOT NULL CHECK (kind IN ('daily', 'level')),
    ref        text NOT NULL,                    -- a nap (daily) vagy a szint (level)
    data       jsonb NOT NULL,
    lang       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    UNIQUE (user_id, kind, ref)
);
CREATE INDEX shares_user_idx ON shares (user_id);
