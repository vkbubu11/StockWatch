-- 016: vizsga es tanusitvany (#37). A vizsga kore (practice_sessions.exam) nem torolheto; a sikeres vizsga
-- tanusitvanya az ellenorzo koddal (exams.id) nyilvanosan visszaellenorizheto.
ALTER TABLE practice_sessions ADD COLUMN exam boolean NOT NULL DEFAULT false;
CREATE TABLE exams (
    id           text PRIMARY KEY,                  -- 12 betu: a tanusitvany ellenorzo kodja
    user_id      bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    session_id   text NOT NULL REFERENCES practice_sessions (id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,                       -- az eredmeny rogzitve (beadas vagy lejart ido)
    score        real,                              -- a jo + balszerencses dontesek aranya
    passed       boolean,
    player       text,                              -- becenev vagy "Jatekos #..." a rogziteskor
    levels       int,                               -- teljesitett leckeszintek a rogziteskor
    levels_total int
);
CREATE INDEX exams_user_idx ON exams (user_id, created_at DESC);
