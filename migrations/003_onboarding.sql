-- 003: az elso belepeskori bevezeto (#39) - a felhasznalo vegigment-e rajta
ALTER TABLE users ADD COLUMN onboarded_at timestamptz;
