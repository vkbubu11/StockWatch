-- 018: gyertyankenti visszajatszas es poziciokezeles (#76): a kor kezelesi modja (alapbol ki - a mai viselkedes)
ALTER TABLE practice_sessions ADD COLUMN manage boolean NOT NULL DEFAULT false;
-- fejezetzaro feladatsor (#79 b): a kor melyik fejezetet zarja, es a fejezetek teljesitese
ALTER TABLE practice_sessions ADD COLUMN boss smallint;
CREATE TABLE chapter_bosses (
    user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chapter smallint NOT NULL,
    best_share real NOT NULL,
    completed_at timestamptz,
    PRIMARY KEY (user_id, chapter)
);
