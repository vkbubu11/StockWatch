-- 001: felhasznalok es bejelentkezes (#31), az ASZF elfogadasa (#40).
-- A tobbi tabla (haladas, jelvenyek, napi kihivas, leckek, ismetles, Google-azonosito) a sajat ticketjevel,
-- kulon migracioban jon. Futtatas: python cli.py db migrate

CREATE TABLE users (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email             text NOT NULL,
    password_hash     text NOT NULL,              -- scrypt, a webauth.hash_password formatumaban
    email_verified_at timestamptz,
    terms_version     text,                       -- az elfogadott ASZF / adatkezelesi tajekoztato verzioja
    terms_accepted_at timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz                 -- torlesre jelolve (#40: 30 napon belul vegleg torlodik)
);
CREATE UNIQUE INDEX users_email_key ON users (lower(email));

-- munkamenet: a sutiben a token, itt csak a hash-e
CREATE TABLE sessions (
    token_hash   bytea PRIMARY KEY,
    user_id      bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    last_used_at timestamptz NOT NULL DEFAULT now(),
    user_agent   text
);
CREATE INDEX sessions_user_idx ON sessions (user_id);

-- e-mail-ellenorzes es jelszo-visszaallitas: egyszer hasznalhato, lejar
CREATE TABLE email_tokens (
    token_hash bytea PRIMARY KEY,
    user_id    bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    purpose    text NOT NULL CHECK (purpose IN ('verify', 'reset')),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    used_at    timestamptz
);
CREATE INDEX email_tokens_user_idx ON email_tokens (user_id);
