-- 014: hibak ismetlese (#36) - helyzettipusonkent (kategoria + forras) noveklo idokozzel
CREATE TABLE review_queue (
    user_id       bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    category      text NOT NULL,
    source        text NOT NULL,
    interval_days int NOT NULL,
    due           date NOT NULL,
    misses        int NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, category, source)
);
