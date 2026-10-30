-- 017: az admin osszegzesbol (analytics.overview) kizart felhasznalok - pl. sajat es tesztfiokok.
ALTER TABLE users ADD COLUMN stats_excluded boolean NOT NULL DEFAULT false;
