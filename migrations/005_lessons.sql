-- 005: tanulasi ut (#33) - a lecke feladatsora melyik szinthez tartozik, es a szintek teljesitese
ALTER TABLE practice_sessions ADD COLUMN lesson int;

CREATE TABLE lesson_progress (
    user_id      bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    level        int NOT NULL,
    best_share   double precision NOT NULL,     -- a legjobb feladatsor jo + balszerencses aranya
    completed_at timestamptz,                   -- az elso sikeres feladatsor ideje (NULL: meg nem teljesitett)
    PRIMARY KEY (user_id, level)
);
