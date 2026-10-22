-- 008: belepes Google-fiokkal (#43). A fiok a Google `sub` azonositojahoz kotodik, nem az e-mailhez.
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;   -- NULL: csak Google-lal lep be

CREATE TABLE user_identities (
    provider   text NOT NULL,                    -- 'google'
    subject    text NOT NULL,                    -- a szolgaltato allando felhasznalo-azonositoja (sub)
    user_id    bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    email      text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, subject)
);
CREATE INDEX user_identities_user_idx ON user_identities (user_id);
