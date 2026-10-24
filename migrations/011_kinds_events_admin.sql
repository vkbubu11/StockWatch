-- 011: feladattipusok (#49-#53), meres (#48), admin felulet (#58)

-- feladattipus: decision (a regi) | stop | pattern | levels | sizing; a tipus parameterei (prompt: a felhasznalo
-- latja, secret: a jo valasz - a felfedes elott nem megy ki); quality: az uj tipusok ertekelese 0..1
ALTER TABLE practice_tasks ADD COLUMN kind text NOT NULL DEFAULT 'decision';
ALTER TABLE practice_tasks ADD COLUMN params jsonb;
ALTER TABLE practice_tasks ADD COLUMN quality double precision;

-- termekesemenyek (#48): IP es e-mail nelkul. subject: 'u<id>' (felhasznalo), torleskor 'x<veletlen>'
-- (anonimizalva), NULL: nevtelen (oldalmegtekintes, hibaszamlalo). A nyers esemenyek 13 honapig maradnak.
CREATE TABLE events (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at      timestamptz NOT NULL DEFAULT now(),
    name    text NOT NULL,
    subject text,
    props   jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX events_name_at_idx ON events (name, at);
CREATE INDEX events_subject_idx ON events (subject);

-- admin (#58): szerepkor, ketlepcsos azonositas (TOTP), felfuggesztes; az admin munkamenet a TOTP-ellenorzes ideje
ALTER TABLE users ADD COLUMN role text CHECK (role IN ('admin'));
ALTER TABLE users ADD COLUMN totp_secret text;
ALTER TABLE users ADD COLUMN totp_last_step bigint;           -- NULL: a beallitas meg nincs megerositve
ALTER TABLE users ADD COLUMN suspended_at timestamptz;
ALTER TABLE sessions ADD COLUMN admin_at timestamptz;

-- audit-naplo: csak hozzafuzheto (a modositast es a torlest trigger tiltja); felhasznalo-torles utan is megmarad
CREATE TABLE admin_audit (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    admin_id    bigint NOT NULL,
    action      text NOT NULL,
    target_user bigint,
    details     jsonb NOT NULL DEFAULT '{}'
);
CREATE FUNCTION admin_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'admin_audit: csak hozzafuzheto';
END $$;
CREATE TRIGGER admin_audit_no_change BEFORE UPDATE OR DELETE ON admin_audit
    FOR EACH ROW EXECUTE FUNCTION admin_audit_append_only();

-- leckeszerkeszto: verziok (a legujabb ervenyes; ha nincs, a locale/lessons.<nyelv>.json)
CREATE TABLE lesson_versions (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lang       text NOT NULL,
    data       jsonb NOT NULL,
    note       text,
    admin_id   bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- napi kihivas: egy nap helyzetsoranak csereje (hibas feladat eseten), naplozva
CREATE TABLE daily_overrides (
    day        date PRIMARY KEY,
    seed       bigint NOT NULL,
    admin_id   bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
