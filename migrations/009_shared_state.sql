-- 009: tobb webes folyamat kozott megosztott allapot (eddig folyamatonkent a memoriaban)
-- gyakorisag-korlat (belepes, regisztracio, visszaallitas): egy sor = egy esemeny; az ablakon kivuliek torlodnek
CREATE TABLE rate_hits (
    key text NOT NULL,                          -- "<korlat neve>:<IP vagy e-mail>"
    at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX rate_hits_key_idx ON rate_hits (key, at);
CREATE INDEX rate_hits_at_idx ON rate_hits (at);

-- fuggo Google-belepesek (#43): egyszer hasznalhato, rovid eletu; a state csak hash-kent
CREATE TABLE oauth_states (
    state_hash bytea PRIMARY KEY,
    nonce      text NOT NULL,
    verifier   text NOT NULL,                   -- PKCE code_verifier
    link_user  bigint REFERENCES users (id) ON DELETE CASCADE,
    terms      boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
