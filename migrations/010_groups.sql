-- 010: privat csoport-ranglista meghivokoddal (#47)
CREATE TABLE learning_groups (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       text NOT NULL,
    code       text NOT NULL UNIQUE,                -- meghivokod (betuk)
    owner_id   bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE group_members (
    group_id  bigint NOT NULL REFERENCES learning_groups (id) ON DELETE CASCADE,
    user_id   bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    joined_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, user_id)
);
CREATE INDEX group_members_user_idx ON group_members (user_id);
