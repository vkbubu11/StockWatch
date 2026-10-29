-- 015: heti ligak (#56) - hetfotol vasarnapig (Europe/Budapest) a heti pont szamit, kb. 30 fos csoportokban;
-- a het zarasakor a legjobbak feljutnak, a leggyengebbek kiesnek. A heti pont a practice_tasks pontjaibol jon.
CREATE TABLE league_members (
    user_id      bigint PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    tier         smallint NOT NULL DEFAULT 0,          -- 0 Bronz, 1 Ezust, 2 Arany, 3 Platina, 4 Gyemant
    opted_out_at timestamptz                           -- kilepett: nem kerul csoportba
);
CREATE TABLE league_entries (
    week    date NOT NULL,                              -- a het hetfoje
    user_id bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    tier    smallint NOT NULL,
    grp     int NOT NULL,
    xp      int,                                        -- a het zarasakor rogzitve
    rank    int,
    outcome text CHECK (outcome IN ('up', 'stay', 'down')),
    seen_at timestamptz,                                -- a feluleten latta az eredmenyt
    PRIMARY KEY (week, user_id)
);
CREATE INDEX league_entries_group_idx ON league_entries (week, tier, grp);
