-- Runs once, the first time the Postgres volume is created.
-- The automated tests use their own database so they never touch demo data.
CREATE DATABASE ops_test;
